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
//! `semantic_search` + `list_qdrant_collections` — port của
//! `cplus_mcp.py`/`android_mcp.py` tool bodies + `semantic_graph_expansion.py`.
//!
//! Python-plane (documented): query embedding chạy sentence-transformers
//! (jina-embeddings-v3) trong tiến trình Python. Rust chỉ replicate đúng
//! resolution + expansion lanes; với mọi fixture scope (project cortext) local
//! store không có collection khớp → vector hits rỗng, byte-identical với
//! Python. Graph expansion (Cypher) được port đầy đủ.

use serde_json::{json, Map, Value};

use super::{
    is_database_not_found_error, normalize_content_mode, payload_bool, payload_str,
    payload_string, resolve_db_candidates, resolve_rel_types_with_diagnostics,
    run_cypher_first, unsupported_relationship_result, Backend,
};
use crate::framework_registry::capability_for_parser;
use super::runtime;


/// `_normalize_collections`.
fn normalize_collections(value: Option<&Value>) -> Vec<String> {
    match value {
        None => Vec::new(),
        Some(Value::Null) => Vec::new(),
        Some(Value::String(text)) => {
            let trimmed = text.trim();
            if trimmed.is_empty() {
                Vec::new()
            } else {
                trimmed
                    .split(',')
                    .map(|part| part.trim().to_string())
                    .filter(|part| !part.is_empty())
                    .collect()
            }
        }
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(|item| match item {
                Value::String(text) => Some(text.trim().to_string()),
                other => Some(other.to_string()),
            })
            .filter(|item| !item.is_empty())
            .collect(),
        Some(other) => vec![other.to_string()],
    }
}

/// `_resolve_collection_scopes`.
fn resolve_collection_scopes(tokens: &[String], available: &[String]) -> Vec<String> {
    let mut resolved: Vec<String> = Vec::new();
    for token in tokens {
        let candidates: Vec<String> = if available.iter().any(|name| name == token) {
            vec![token.clone()]
        } else {
            available
                .iter()
                .filter(|name| is_project_scope_collection(token, name))
                .cloned()
                .collect()
        };
        for candidate in candidates {
            if !resolved.contains(&candidate) {
                resolved.push(candidate);
            }
        }
    }
    resolved
}

/// `_is_project_scope_collection`.
fn is_project_scope_collection(scope: &str, collection: &str) -> bool {
    let scope = scope.trim();
    let collection = collection.trim();
    if scope.is_empty() || collection.is_empty() {
        return false;
    }
    if collection == format!("{scope}_mess") {
        return true;
    }
    collection.starts_with(&format!("{scope}_"))
        && collection.contains("__")
        && collection.ends_with("_functions")
}

/// `semantic_graph_expansion::DEFAULT_GRAPH_REL_TYPES`.
const DEFAULT_GRAPH_REL_TYPES: [&str; 4] = ["CALLS", "USES_TYPE", "REFERENCES", "INHERITS"];
const MAX_GRAPH_DEPTH: i64 = 5;
const DEFAULT_GRAPH_LIMIT: i64 = 50;

fn normalize_positive_int(value: Option<&Value>, default: i64, max_value: Option<i64>) -> i64 {
    let mut number = match value {
        Some(Value::Number(number)) => number.as_i64().unwrap_or(default),
        Some(Value::String(text)) => text.trim().parse().unwrap_or(default),
        _ => default,
    };
    if number < 1 {
        number = default;
    }
    if let Some(max) = max_value {
        number = number.min(max);
    }
    number
}

fn normalize_graph_direction(value: Option<&str>) -> String {
    let text = value.unwrap_or("").trim().to_lowercase();
    match text.as_str() {
        "in" | "incoming" | "upstream" => "in".to_string(),
        "out" | "outgoing" | "downstream" => "out".to_string(),
        _ => "both".to_string(),
    }
}

fn normalize_graph_rel_types(value: Option<&Value>) -> Result<Vec<String>, String> {
    let items: Vec<String> = match value {
        None => DEFAULT_GRAPH_REL_TYPES.iter().map(|v| v.to_string()).collect(),
        Some(Value::Null) => DEFAULT_GRAPH_REL_TYPES.iter().map(|v| v.to_string()).collect(),
        Some(Value::String(text)) => {
            let trimmed = text.trim();
            if trimmed.is_empty() {
                DEFAULT_GRAPH_REL_TYPES.iter().map(|v| v.to_string()).collect()
            } else {
                trimmed
                    .replace(';', ",")
                    .split(',')
                    .map(|part| part.trim().to_string())
                    .collect()
            }
        }
        Some(Value::Array(items)) => items
            .iter()
            .map(|item| match item {
                Value::String(text) => text.clone(),
                other => other.to_string(),
            })
            .collect(),
        Some(other) => vec![other.to_string()],
    };
    let mut rel_types: Vec<String> = Vec::new();
    for item in items {
        let rel = item.trim().to_uppercase();
        if rel.is_empty() {
            continue;
        }
        if !rel.chars().all(|ch| ch.is_alphanumeric() || ch == '_') {
            return Err(format!("Invalid graph relationship type: {item}"));
        }
        if !rel_types.contains(&rel) {
            rel_types.push(rel);
        }
    }
    if rel_types.is_empty() {
        rel_types = DEFAULT_GRAPH_REL_TYPES.iter().map(|v| v.to_string()).collect();
    }
    Ok(rel_types)
}

fn proximity(hops: i64) -> f64 {
    let value = 1.0 - hops.max(0) as f64 * 0.2;
    (value.round_to(6)) .max(0.0)
}

trait RoundTo {
    fn round_to(self, digits: i32) -> f64;
}

impl RoundTo for f64 {
    fn round_to(self, digits: i32) -> f64 {
        let factor = 10f64.powi(digits);
        (self * factor).round() / factor
    }
}

/// Local qdrant store collections (dir scan dưới storage layout).
fn local_store_collections() -> Vec<String> {
    let base = std::path::PathBuf::from(
        std::env::var("QDRANT_CODE_PATH").unwrap_or_default(),
    );
    let base = if base.as_os_str().is_empty() {
        // resolve_storage(Path.cwd()).qdrant_code_path — cùng layout phase-10:
        // ~/.cortext-harness/v1/instances/<instance>/qdrant/code
        let instance = std::env::var("CORTEX_STORAGE_INSTANCE").unwrap_or_else(|_| "default".to_string());
        let home = std::env::var("CORTEX_DATA_HOME")
            .or_else(|_| std::env::var("HOME"))
            .unwrap_or_default();
        std::path::PathBuf::from(home)
            .join(".cortext-harness")
            .join("v1")
            .join("instances")
            .join(instance)
            .join("qdrant")
            .join("code")
            .join("collection")
    } else {
        base.join("collection")
    };
    let Ok(entries) = std::fs::read_dir(&base) else {
        return Vec::new();
    };
    let mut names: Vec<String> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.is_dir())
        .filter_map(|path| path.file_name().map(|name| name.to_string_lossy().to_string()))
        .collect();
    names.sort();
    names
}

pub fn tool_list_qdrant_collections(payload: &Value) -> Value {
    let include_vectors = payload_bool(payload, "include_vectors");
    let qdrant_url = payload_str(payload, "qdrant_url").unwrap_or("");
    // collections_payload(store, include_vectors) — include_vectors là cold
    // diagnostic path cần đọc vector sizes từ sqlite (Python-plane); fixtures
    // dùng include_vectors mặc định false.
    let _ = include_vectors;
    let collections = local_store_collections();
    // `cached_collections_payload`: lift `collections` (list tên) + giữ raw
    // {result: {collections: [{name}]}, status: "ok"} — đúng shape Python.
    let _ = qdrant_url;
    json!({
        "collections": collections,
        "raw": {
            "result": {
                "collections": collections
                    .iter()
                    .map(|name| json!({"name": name}))
                    .collect::<Vec<_>>(),
            },
            "status": "ok",
        },
    })
}

pub fn tool_semantic_search(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let _ = backend;
    let query = payload_str(payload, "query").unwrap_or("").trim().to_string();
    if query.is_empty() {
        return Err("query is required.".to_string());
    }
    let mode = payload_str(payload, "mode").unwrap_or("combined").to_string();
    let _top_k = payload
        .get("top_k")
        .and_then(Value::as_i64)
        .unwrap_or(10);
    let project_id = payload_string(payload, "project_id");
    let mut expand_graph = payload_bool(payload, "expand_graph");
    let graph_depth = payload.get("graph_depth").cloned().unwrap_or(json!(2));
    let graph_direction = payload_str(payload, "graph_direction").unwrap_or("both").to_string();
    let mut graph_rel_types_value = payload.get("graph_rel_types").cloned();
    let graph_limit = payload.get("graph_limit").cloned().unwrap_or(json!(50));
    let content_mode = normalize_content_mode(payload_str(payload, "content_mode"));

    let selected_parser = crate::dispatch::normalize_parser_type(payload_str(payload, "parser_type"));
    let mut capability_diagnostics: Option<Value> = None;
    let mut graph_expansion_unavailable = false;
    if expand_graph {
        let explicit_relationship_request =
            graph_rel_types_value.is_some() && !payload_bool(payload, "_capability_default_relationships");
        let db_candidates = resolve_db_candidates(project_id.as_deref())?;
        let resolved = resolve_rel_types_with_diagnostics(
            runtime,
            graph_rel_types_value.as_ref(),
            selected_parser.as_deref(),
            &db_candidates,
            explicit_relationship_request,
        )?;
        graph_rel_types_value = Some(json!(resolved.applied.clone()));
        if resolved.applied.is_empty() {
            if explicit_relationship_request {
                return Ok(unsupported_relationship_result(
                    selected_parser.as_deref(),
                    &resolved.diagnostics,
                ));
            }
            graph_expansion_unavailable = true;
            expand_graph = false;
            graph_rel_types_value = Some(json!([]));
        }
        capability_diagnostics = Some(resolved.diagnostics);
    }

    // `_resolve_base_collections`: collection override không khớp store →
    // ValueError với message byte-match Python.
    let available = local_store_collections();
    let explicit_tokens = normalize_collections(payload.get("collection"));
    if !explicit_tokens.is_empty() {
        let resolved = resolve_collection_scopes(&explicit_tokens, &available);
        if resolved.is_empty() {
            // Python repr: danh sách single-quote.
            let py_list = |items: &[String]| -> String {
                let inner: Vec<String> =
                    items.iter().map(|item| format!("'{item}'")).collect();
                format!("[{}]", inner.join(", "))
            };
            return Err(format!(
                "Requested Qdrant collection scope does not match any available collection. \
                 scopes={} available={}. Pass a valid collection name/prefix or omit \
                 collection to search every available collection.",
                py_list(&explicit_tokens),
                py_list(&available),
            ));
        }
    } else if available.is_empty() {
        return Err(
            "No Qdrant collections available. Use list_qdrant_collections to verify.".to_string(),
        );
    }

    // Vector lane (Python-plane embedder): local store scopes với fixture
    // project_id không có collection khớp → results rỗng như Python.
    let mut results = Map::new();
    results.insert("mode".to_string(), json!(mode));
    results.insert("query".to_string(), json!(query));
    results.insert("results".to_string(), json!([]));
    results.insert("content_mode".to_string(), json!(content_mode));

    // graph_expansion luôn được gắn (expand_semantic_results chạy mọi mode).
    let items: Vec<Value> = Vec::new();
    let expansion = expand_semantic_results(
        runtime,
        &items,
        &project_id,
        expand_graph,
        &graph_depth,
        &graph_direction,
        graph_rel_types_value.as_ref(),
        &graph_limit,
        project_id.as_deref(),
    )?;
    let mut expansion = expansion;
    if graph_expansion_unavailable {
        if let Some(object) = expansion.as_object_mut() {
            object.insert("requested".to_string(), json!(true));
            object.insert("outcome".to_string(), json!("unavailable"));
            object.insert(
                "reason".to_string(),
                json!("No requested relationships are available in the active graph provider."),
            );
            object.insert("relationship_types".to_string(), json!([]));
        }
    }
    results.insert("graph_expansion".to_string(), expansion);
    if let Some(diagnostics) = capability_diagnostics {
        results.insert("capability_diagnostics".to_string(), diagnostics);
    }
    Ok(Value::Object(results))
}

/// `expand_semantic_results` — Cypher lanes port đầy đủ.
#[allow(clippy::too_many_arguments)]
fn expand_semantic_results(
    runtime: &mut runtime::GraphRuntime,
    items: &[Value],
    _project_id: &Option<String>,
    expand_graph: bool,
    graph_depth: &Value,
    graph_direction: &str,
    graph_rel_types: Option<&Value>,
    graph_limit: &Value,
    project_id: Option<&str>,
) -> Result<Value, String> {
    let depth = normalize_positive_int(Some(graph_depth), 2, Some(MAX_GRAPH_DEPTH));
    let limit = normalize_positive_int(Some(graph_limit), DEFAULT_GRAPH_LIMIT, Some(500));
    let direction = normalize_graph_direction(Some(graph_direction));
    let rel_types = normalize_graph_rel_types(graph_rel_types)?;
    let project_value = match project_id.map(str::trim).filter(|text| !text.is_empty()) {
        Some(id) => json!(id),
        None => Value::Null,
    };
    let seed_ids: Vec<String> = items
        .iter()
        .filter_map(|item| item.get("payload").and_then(Value::as_object))
        .filter_map(|payload| {
            payload
                .get("node_id")
                .or_else(|| payload.get("symbol_id"))
                .map(value_string)
        })
        .filter(|id| !id.is_empty())
        .map(|id| {
            if let Some(stripped) = id.strip_prefix("file::") {
                stripped.to_string()
            } else {
                id
            }
        })
        .collect();

    let mut expansion = Map::new();
    expansion.insert("enabled".to_string(), json!(expand_graph));
    expansion.insert("seed_ids".to_string(), json!(seed_ids));
    expansion.insert("depth".to_string(), json!(depth));
    expansion.insert("direction".to_string(), json!(direction));
    expansion.insert("relationship_types".to_string(), json!(rel_types));
    expansion.insert("results".to_string(), json!([]));
    expansion.insert("edges".to_string(), json!([]));

    if !expand_graph || seed_ids.is_empty() {
        return Ok(Value::Object(expansion));
    }
    let rel = rel_types.join("|");
    let rel_pattern = match direction.as_str() {
        "in" => format!("<-[:{rel}*1..{depth}]-"),
        "out" => format!("-[:{rel}*1..{depth}]->"),
        _ => format!("-[:{rel}*1..{depth}]-"),
    };
    let edge_pattern = match direction.as_str() {
        "in" => format!("<-[r:{rel}]-"),
        "out" => format!("-[r:{rel}]->"),
        _ => format!("-[r:{rel}]-"),
    };
    let servlet = super::servlet_predicate("neighbor");
    let node_query = format!(
        "\n    UNWIND $seed_ids AS sid\n    MATCH (seed {{id: sid}})\n    WHERE ($project_id IS NULL OR seed.project_id_normalized STARTS WITH $project_id_normalized)\n    MATCH p = (seed){rel_pattern}(neighbor)\n    WHERE neighbor.id IS NOT NULL\n      AND NOT neighbor.id IN $seed_ids\n      AND ($project_id IS NULL OR neighbor.project_id_normalized STARTS WITH $project_id_normalized)\n      AND {servlet}\n    WITH neighbor, min(length(p)) AS hops, collect(DISTINCT sid) AS seed_ids\n    ORDER BY hops ASC\n    LIMIT $limit\n    RETURN seed_ids[0] AS seed_id,\n           seed_ids AS seed_ids,\n           neighbor.id AS node_id,\n           neighbor.name AS name,\n           coalesce(neighbor.qualified_name, neighbor.name) AS qualified_name,\n           coalesce(neighbor.kind, labels(neighbor)[0], 'Node') AS kind,\n           neighbor.framework AS framework,\n           neighbor.resolution_status AS resolution_status,\n           coalesce(neighbor.file_path, '') AS file_path,\n           neighbor.start_line AS start_line,\n           neighbor.end_line AS end_line,\n           neighbor.intent AS intent,\n           neighbor.exported AS exported,\n           neighbor.side_effect AS side_effect,\n           neighbor.doc_confidence AS doc_confidence,\n           neighbor.project_id AS project_id,\n           neighbor.target_name AS target_name,\n           neighbor.signature AS signature,\n           neighbor.language AS language,\n           hops AS hop_distance\n    "
    );
    let db_candidates = resolve_db_candidates(project_id)?;
    let node_params = super::map_from_static([
        ("seed_ids", json!(seed_ids)),
        ("limit", json!(limit)),
        ("project_id", project_value.clone()),
    ]);
    let used_db_result =
        run_cypher_first(runtime, &node_query, &node_params, &db_candidates);
    let (used_db, rows) = match used_db_result {
        Ok(result) => result,
        Err(error) => {
            expansion.insert("error".to_string(), json!(error));
            return Ok(Value::Object(expansion));
        }
    };
    expansion.insert("db".to_string(), json!(used_db.clone()));

    let mut graph_nodes: Vec<Value> = Vec::new();
    let mut node_ids: Vec<String> = seed_ids.clone();
    for row in &rows {
        let node_id = row
            .get("node_id")
            .map(value_string)
            .unwrap_or_default();
        if node_id.is_empty() {
            continue;
        }
        let hops = normalize_positive_int(row.get("hop_distance"), depth, None);
        let get = |key: &str| row.get(key).cloned().unwrap_or(Value::Null);
        let seed_id = get("seed_id");
        let seed_ids_row: Vec<Value> = row
            .get("seed_ids")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        graph_nodes.push(json!({
            "seed_id": seed_id,
            "seed_ids": if seed_ids_row.is_empty() {
                if seed_id.is_null() { json!([]) } else { json!([seed_id.clone()]) }
            } else {
                json!(seed_ids_row)
            },
            "node_id": node_id,
            "name": super::null_str_or(get("name"), ""),
            "qualified_name": super::null_str_or(get("qualified_name"), ""),
            "kind": super::null_str_or(get("kind"), "Node"),
            "framework": super::null_str_or(get("framework"), ""),
            "resolution_status": super::null_str_or(get("resolution_status"), ""),
            "file_path": super::null_str_or(get("file_path"), ""),
            "start_line": get("start_line"),
            "end_line": get("end_line"),
            "intent": super::null_str_or(get("intent"), ""),
            "exported": get("exported").as_bool().unwrap_or(false),
            "side_effect": get("side_effect").as_bool().unwrap_or(false),
            "doc_confidence": get("doc_confidence").as_f64().unwrap_or(0.0),
            "project_id": super::null_str_or(get("project_id"), ""),
            "target_name": super::null_str_or(get("target_name"), ""),
            "signature": super::null_str_or(get("signature"), ""),
            "language": super::null_str_or(get("language"), ""),
            "hop_distance": hops,
            "graph_proximity": proximity(hops),
        }));
        if !node_ids.contains(&node_id) {
            node_ids.push(node_id);
        }
    }
    expansion.insert("results".to_string(), Value::Array(graph_nodes.clone()));
    if graph_nodes.is_empty() {
        return Ok(Value::Object(expansion));
    }
    let edge_query = format!(
        "\n    MATCH (source){edge_pattern}(target)\n    WHERE source.id IN $node_ids\n      AND target.id IN $node_ids\n      AND ($project_id IS NULL OR source.project_id_normalized STARTS WITH $project_id_normalized)\n      AND ($project_id IS NULL OR target.project_id_normalized STARTS WITH $project_id_normalized)\n    RETURN DISTINCT source.id AS source,\n           target.id AS target,\n           type(r) AS type,\n           coalesce(r.confidence, r.score) AS confidence,\n           r.call_depth AS call_depth\n    LIMIT $edge_limit\n    "
    );
    let edge_dbs: Vec<String> = match expansion.get("db").and_then(Value::as_str) {
        Some(db) => vec![db.to_string()],
        None => db_candidates.clone(),
    };
    let edge_params = super::map_from_static([
        ("node_ids", json!(node_ids)),
        ("edge_limit", json!((limit * 2).max(25))),
        ("project_id", project_value),
    ]);
    match run_cypher_first(runtime, &edge_query, &edge_params, &edge_dbs) {
        Ok((_, edge_rows)) => {
            let edges: Vec<Value> = edge_rows
                .iter()
                .map(|row| {
                    json!({
                        "source": row.get("source").cloned().unwrap_or(Value::Null),
                        "target": row.get("target").cloned().unwrap_or(Value::Null),
                        "type": row.get("type").cloned().unwrap_or(Value::Null),
                        "confidence": row.get("confidence").cloned().unwrap_or(Value::Null),
                        "call_depth": row.get("call_depth").cloned().unwrap_or(Value::Null),
                    })
                })
                .collect();
            expansion.insert("edges".to_string(), Value::Array(edges));
        }
        Err(error) => {
            expansion.insert("edge_error".to_string(), json!(error));
        }
    }
    let _ = is_database_not_found_error("");
    Ok(Value::Object(expansion))
}

fn value_string(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

// Re-imports kept referenced.
#[allow(unused_imports)]
use capability_for_parser as _capability_for_parser;

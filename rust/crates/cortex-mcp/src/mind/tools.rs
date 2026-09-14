// Faithful port của Python tool bodies trong `doc-tiny/mcp_graph_rag.py` —
// cấu trúc nguồn được giữ để đối chiếu parity; các lint style dưới đây được
// allow có chủ đích ở module mind.
#![allow(
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::needless_borrow,
    clippy::map_clone,
    clippy::let_and_return,
    clippy::unwrap_or_default
)]
//! Mind tools: `semantic_search`, `query_graph_rag_langextract`,
//! `list_source_ids`, `list_qdrant_collections`, `get_paragraph_text`
//! (bodies of `register_tools` + the shared helpers
//! `qdrant_search_entity_payload`, `_compute_heuristic_rerank_score`,
//! `_apply_heuristic_rerank`, `_filter_entity_ids_for_expansion`).

use std::collections::{BTreeMap, BTreeSet};

use cortex_falkordb::client::Param;
use serde_json::{json, Map, Value};

use super::graphstore::{self, GraphError};
use super::qdrant::{self, QdrantBackend, QdrantError};
use crate::project_registry;

/// Unified tool error: exception name + message (`_standard_tool` maps it
/// through `normalize_error`).
pub struct ToolError {
    pub exception: &'static str,
    pub message: String,
}

impl ToolError {
    pub fn value_error(message: impl Into<String>) -> Self {
        Self { exception: "ValueError", message: message.into() }
    }

    /// Embedding sidecar failure — mirrors an in-process encoder raising.
    pub fn embed(message: impl Into<String>) -> Self {
        Self { exception: "RuntimeError", message: message.into() }
    }
}

impl From<QdrantError> for ToolError {
    fn from(error: QdrantError) -> Self {
        ToolError { exception: error.exception, message: error.message }
    }
}

impl From<GraphError> for ToolError {
    fn from(error: GraphError) -> Self {
        let exception = if error.exception == "RuntimeError" {
            // Driver exceptions surface through `str(exc)` like redis-py.
            "RuntimeError"
        } else {
            error.exception
        };
        ToolError { exception, message: error.message }
    }
}

pub fn value_to_param(value: &Value) -> Param {
    super::super::graph::runtime::json_to_param(value)
}

// ---------------------------------------------------------------------------
// Type coercion (n8n-style string inputs)
// ---------------------------------------------------------------------------

/// `_coerce_bool`.
pub fn coerce_bool(value: Option<&Value>, default: bool) -> Result<bool, ToolError> {
    let Some(value) = value else { return Ok(default) };
    match value {
        Value::Null => Ok(default),
        Value::Bool(flag) => Ok(*flag),
        Value::Number(number) => Ok(number.as_f64().map(|f| f != 0.0).unwrap_or(false)),
        Value::String(text) => {
            let lowered = text.trim().to_lowercase();
            match lowered.as_str() {
                "1" | "true" | "yes" | "on" => Ok(true),
                "0" | "false" | "no" | "off" | "" => Ok(false),
                _ => Err(ToolError::value_error(format!(
                    "invalid boolean value: {}",
                    python_repr(value)
                ))),
            }
        }
        other => Ok(!matches!(other, Value::Null)),
    }
}

/// Python `repr` of a scalar JSON value (error messages embed `{value!r}`).
pub fn python_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(number) => number.to_string(),
        Value::String(text) => format!("'{text}'"),
        other => other.to_string(),
    }
}

/// Python `int(value)` — `None` passthrough handled by the caller.
fn py_int(value: &Value) -> Result<i64, ToolError> {
    match value {
        Value::Number(number) => {
            if let Some(int) = number.as_i64() {
                Ok(int)
            } else {
                Ok(number.as_f64().map(|f| f.trunc() as i64).unwrap_or(0))
            }
        }
        Value::Bool(flag) => Ok(i64::from(*flag)),
        Value::String(text) => {
            let trimmed = text.trim();
            let normalized = trimmed
                .strip_prefix('+')
                .unwrap_or(trimmed);
            if let Ok(int) = normalized.parse::<i64>() {
                return Ok(int);
            }
            // Python also accepts float literals via int(str) → ValueError.
            Err(ToolError::value_error(format!(
                "invalid literal for int() with base 10: {}",
                python_repr(value)
            )))
        }
        other => Err(ToolError::value_error(format!(
            "int() argument must be a string, a bytes-like object or a real number, not '{}'",
            python_type_name(other)
        ))),
    }
}

fn python_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(_) => "float",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// Python `float(value)`.
fn py_float(value: &Value) -> Result<f64, ToolError> {
    match value {
        Value::Number(number) => Ok(number.as_f64().unwrap_or(0.0)),
        Value::Bool(flag) => Ok(if *flag { 1.0 } else { 0.0 }),
        Value::String(text) => text
            .trim()
            .parse::<f64>()
            .map_err(|_| ToolError::value_error(format!("could not convert string to float: {}", python_repr(value)))),
        other => Err(ToolError::value_error(format!(
            "float() argument must be a string or a real number, not '{}'",
            python_type_name(other)
        ))),
    }
}

/// `str(value) if value else ""` for the query coercion.
fn coerce_query(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(text)) if text.is_empty() => String::new(),
        Some(Value::Bool(false)) => String::new(),
        Some(Value::Number(number)) if number.as_f64() == Some(0.0) => String::new(),
        Some(other) => match other {
            Value::String(text) => text.clone(),
            Value::Bool(flag) => {
                if *flag {
                    "True".to_string()
                } else {
                    "False".to_string()
                }
            }
            scalar => scalar.to_string(),
        },
    }
}

fn optional_string(value: Option<&Value>) -> Option<String> {
    match value {
        None | Some(Value::Null) => None,
        Some(Value::String(text)) if text.trim().is_empty() => None,
        Some(Value::String(text)) => Some(text.clone()),
        Some(other) => Some(other.to_string()),
    }
}

// ---------------------------------------------------------------------------
// Project/collection resolution
// ---------------------------------------------------------------------------

/// `_resolve_doc_collection(project_id, collection)` — the single canonical
/// collection reported in tool output. Unscoped + no override → the
/// `QDRANT_COLLECTION_DOC` default constant (`documents`), exactly like the
/// Python helper.
pub fn resolve_doc_collection(
    project_id: Option<&str>,
    collection: Option<&str>,
) -> String {
    if let Some(collection) = collection.map(str::trim).filter(|value| !value.is_empty()) {
        return collection.to_string();
    }
    if project_id.map(str::trim).filter(|value| !value.is_empty()).is_some() {
        return resolve_doc_collections(project_id, collection)
            .into_iter()
            .next()
            .unwrap_or_else(|| QDRANT_COLLECTION.to_string());
    }
    QDRANT_COLLECTION.to_string()
}

/// `QDRANT_COLLECTION_DOC` default.
const QDRANT_COLLECTION: &str = "documents";

/// `_resolve_doc_collections(project_id, collection)`.
pub fn resolve_doc_collections(
    project_id: Option<&str>,
    collection: Option<&str>,
) -> Vec<String> {
    if let Some(collection) = collection.map(str::trim).filter(|value| !value.is_empty()) {
        return vec![collection.to_string()];
    }
    if let Some(project_id) = project_id.map(str::trim).filter(|value| !value.is_empty()) {
        // doc-tiny project_contract registry (`{id}_doc` + doc.env overrides).
        match project_registry::resolve_project_scope_candidates(Some(project_id), None) {
            Ok(targets) if !targets.is_empty() => {
                let mut names: Vec<String> = Vec::new();
                for target in &targets {
                    let name = target.doc_qdrant_collection.clone();
                    if !name.is_empty() && !names.contains(&name) {
                        names.push(name);
                    }
                }
                if !names.is_empty() {
                    return names;
                }
                return vec![project_id.trim().to_string() + "_doc"];
            }
            _ => {
                // Naming-convention fallback (mirror of the doc-tiny
                // registry: no registered match → `{id}_doc`).
                return vec![project_id.trim().to_string() + "_doc"];
            }
        }
    }
    let mut collections: Vec<String> = Vec::new();
    if let Ok(registered) = project_registry::list_registered_projects(None) {
        for project in &registered {
            if let Ok(targets) = project_registry::resolve_project_targets(Some(project), None) {
                let name = targets.doc_qdrant_collection;
                if !name.is_empty() && !collections.contains(&name) {
                    collections.push(name);
                }
            }
        }
    }
    if collections.is_empty() {
        vec![QDRANT_COLLECTION.to_string()]
    } else {
        collections
    }
}

/// `qdrant_project_filter(project_id)` — the LIKE/prefix payload filter.
fn qdrant_project_filter(project_id: Option<&str>) -> Option<Value> {
    let keys = project_id_scope_keys(project_id)?;
    Some(json!({
        "must": [
            { "key": "project_id_normalized", "match": { "any": keys } },
        ],
    }))
}

/// `project_id_scope_keys` (doc-tiny variant: best-effort known ids).
fn project_id_scope_keys(project_id: Option<&str>) -> Option<Vec<String>> {
    let lookup = cortex_graph_writer::project_scope::project_id_lookup_key(project_id)?;
    let mut keys = vec![lookup.clone()];
    if let Ok(registered) = project_registry::list_registered_projects(None) {
        for id in registered {
            if let Some(known_key) = cortex_graph_writer::project_scope::project_id_lookup_key(Some(&id))
                && known_key.starts_with(&lookup)
                && !keys.contains(&known_key)
            {
                keys.push(known_key);
            }
        }
    }
    keys.sort();
    Some(keys)
}

/// `get_qdrant(project_id)` — per-project backend resolution.
fn get_qdrant(project_id: Option<&str>) -> Result<QdrantBackend, ToolError> {
    qdrant::resolve_backend(project_id).map_err(ToolError::from)
}

// ---------------------------------------------------------------------------
// qdrant_search_entity_payload
// ---------------------------------------------------------------------------

/// One deduped passage row (`payloads_by_key` values).
pub struct PayloadRow {
    pub score: f64,
    pub text: Option<String>,
    pub source_id: Option<String>,
    pub paragraph_id: Option<Value>,
    pub project_id: Option<String>,
    pub project_id_normalized: Option<String>,
    pub entity_ids: Vec<Value>,
    pub entity_mentions: Vec<Value>,
    pub collection: String,
}

/// `qdrant_search_entity_payload` — multi-collection vector search + dedup.
pub fn qdrant_search_entity_payload(
    query_vector: &[f64],
    top_k: usize,
    source_id: Option<&str>,
    collection: Option<&str>,
    project_id: Option<&str>,
) -> Result<Vec<PayloadRow>, ToolError> {
    let backend = get_qdrant(project_id)?;
    let collection_names = resolve_doc_collections(project_id, collection);

    // Qdrant filter: source_id AND project-scope predicates.
    let mut must_conditions: Vec<Value> = Vec::new();
    if let Some(source_id) = source_id.map(str::trim).filter(|value| !value.is_empty()) {
        must_conditions.push(json!({
            "key": "source_id",
            "match": { "value": source_id },
        }));
    }
    if let Some(project_filter) = qdrant_project_filter(project_id)
        && let Some(must) = project_filter.get("must").and_then(Value::as_array)
    {
        for condition in must {
            let Some(key) = condition.get("key").and_then(Value::as_str) else {
                continue;
            };
            let match_spec = condition.get("match").cloned().unwrap_or(Value::Null);
            if let Some(value) = match_spec.get("value") {
                must_conditions.push(json!({ "key": key, "match": { "value": value } }));
            } else if let Some(any) = match_spec.get("any") {
                must_conditions.push(json!({ "key": key, "match": { "any": any } }));
            }
        }
    }
    let qdrant_filter: Option<Value> = if must_conditions.is_empty() {
        None
    } else {
        Some(json!({ "must": must_conditions }))
    };

    let available: Option<Vec<String>> = qdrant::collection_names_cached(&backend);
    if let Some(available) = &available {
        let missing: Vec<&String> = collection_names
            .iter()
            .filter(|name| !available.contains(name))
            .collect();
        if !missing.is_empty() && (project_id.map(|id| !id.trim().is_empty()).unwrap_or(false) || collection.is_some()) {
            let rendered: Vec<String> =
                missing.iter().map(|name| name.to_string()).collect();
            return Err(ToolError::from(QdrantError::lookup(format!(
                "Requested document collection is not ingested or unavailable: {}",
                rendered.join(", ")
            ))));
        }
    }

    // `payloads_by_key` preserves first-insertion order; values replaced on
    // higher score (mirrors the python dict keyed by the payload identity).
    let mut payload_keys: Vec<String> = Vec::new();
    let mut payloads_by_key: Vec<(String, PayloadRow)> = Vec::new();
    for collection_name in &collection_names {
        if let Some(available) = &available
            && !available.contains(collection_name)
        {
            continue;
        }
        let hits = match qdrant::search_points(
            &backend,
            collection_name,
            query_vector,
            top_k,
            qdrant_filter.as_ref(),
        ) {
            Ok(hits) => hits,
            Err(error) => {
                if collection_names.len() == 1 {
                    return Err(ToolError::from(error));
                }
                continue;
            }
        };
        for hit in hits {
            if hit.payload.is_empty() {
                continue;
            }
            let key = format!(
                "{}\u{0}{}\u{0}{}\u{0}{}",
                hit.payload.get("project_id_normalized").map(python_json).unwrap_or_default(),
                hit.payload.get("source_id").map(python_json).unwrap_or_default(),
                hit.payload.get("paragraph_id").map(python_json).unwrap_or_default(),
                hit.payload.get("text").map(python_json).unwrap_or_default(),
            );
            let row = PayloadRow {
                score: hit.score,
                text: hit.payload.get("text").and_then(Value::as_str).map(str::to_string),
                source_id: hit
                    .payload
                    .get("source_id")
                    .and_then(Value::as_str)
                    .map(str::to_string),
                paragraph_id: hit.payload.get("paragraph_id").cloned(),
                project_id: hit
                    .payload
                    .get("project_id")
                    .and_then(Value::as_str)
                    .map(str::to_string),
                project_id_normalized: hit
                    .payload
                    .get("project_id_normalized")
                    .and_then(Value::as_str)
                    .map(str::to_string),
                entity_ids: hit
                    .payload
                    .get("entity_ids")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default(),
                entity_mentions: hit
                    .payload
                    .get("entity_mentions")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default(),
                collection: collection_name.clone(),
            };
            if let Some(position) = payload_keys.iter().position(|existing| *existing == key) {
                let existing = &payloads_by_key[position].1;
                if row.score > existing.score {
                    payloads_by_key[position].1 = row;
                }
            } else {
                payload_keys.push(key.clone());
                payloads_by_key.push((key, row));
            }
        }
    }
    let mut payloads: Vec<PayloadRow> =
        payloads_by_key.into_iter().map(|(_, row)| row).collect();
    // `sort(key=score, reverse=True)` — stable, ties keep insertion order.
    payloads.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
    payloads.truncate(top_k);
    Ok(payloads)
}

/// JSON rendering used only for dedup-key identity (values not emitted).
fn python_json(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

// ---------------------------------------------------------------------------
// Heuristic rerank (pure math — ported exactly)
// ---------------------------------------------------------------------------

/// `_compute_heuristic_rerank_score`.
#[allow(clippy::cast_precision_loss)]
pub fn compute_heuristic_rerank_score(
    passage: &Map<String, Value>,
    entity_types: &[Value],
    entity_weight: f64,
    type_weight: f64,
    confidence_weight: f64,
    length_penalty: f64,
) -> f64 {
    let base_score = passage
        .get("score")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let text = passage.get("text").and_then(Value::as_str).unwrap_or("");
    let mentions = passage
        .get("_entity_mentions")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let mut unique_entities: BTreeSet<String> = BTreeSet::new();
    let mut type_hits: i64 = 0;
    let mut confidences: Vec<f64> = Vec::new();

    for item in &mentions {
        let entity_id = item
            .get("id")
            .and_then(Value::as_str)
            .or_else(|| item.get("name").and_then(Value::as_str));
        if let Some(id) = entity_id
            && !id.is_empty()
        {
            unique_entities.insert(id.to_string());
        }
        if let Some(item_type) = item.get("type")
            && entity_types.contains(item_type)
        {
            type_hits += 1;
        }
        if let Some(confidence) = item.get("confidence")
            && !confidence.is_null() {
                if let Some(conf) = confidence.as_f64() {
                    confidences.push(conf);
                } else if let Some(text) = confidence.as_str()
                    && let Ok(conf) = text.trim().parse::<f64>() {
                        confidences.push(conf);
                    }
            }
    }

    let avg_conf = if confidences.is_empty() {
        0.0
    } else {
        confidences.iter().sum::<f64>() / confidences.len() as f64
    };
    let penalty = if length_penalty != 0.0 { length_penalty * text.chars().count() as f64 } else { 0.0 };
    base_score + entity_weight * unique_entities.len() as f64
        + type_weight * type_hits as f64
        + confidence_weight * avg_conf
        - penalty
}

/// `_apply_heuristic_rerank` — annotate + stable-sort desc by rerank_score.
pub fn apply_heuristic_rerank(
    passages: &mut [Value],
    entity_types: &[Value],
    entity_weight: f64,
    type_weight: f64,
    confidence_weight: f64,
    length_penalty: f64,
) {
    for passage in passages.iter_mut() {
        let Some(object) = passage.as_object_mut() else { continue };
        let score = compute_heuristic_rerank_score(
            object,
            entity_types,
            entity_weight,
            type_weight,
            confidence_weight,
            length_penalty,
        );
        object.insert(
            "rerank_score".to_string(),
            serde_json::Number::from_f64(score).map(Value::Number).unwrap_or(Value::Null),
        );
    }
    passages.sort_by(|a, b| {
        let score_of = |value: &Value| {
            value
                .get("rerank_score")
                .and_then(Value::as_f64)
                .unwrap_or(0.0)
        };
        score_of(b).partial_cmp(&score_of(a)).unwrap_or(std::cmp::Ordering::Equal)
    });
}

// ---------------------------------------------------------------------------
// Expansion filters
// ---------------------------------------------------------------------------

/// `_filter_entity_ids_for_expansion`.
pub fn filter_entity_ids_for_expansion(
    entity_ids: &[String],
    payloads: &[PayloadRow],
    min_score_to_expand: Option<f64>,
    min_entity_occurrences: Option<i64>,
) -> Vec<String> {
    if entity_ids.is_empty() {
        return Vec::new();
    }
    if let Some(min_score) = min_score_to_expand {
        let scores: Vec<f64> = payloads.iter().map(|row| row.score).collect();
        if !scores.is_empty()
            && scores.iter().cloned().fold(f64::NEG_INFINITY, f64::max) < min_score
        {
            return Vec::new();
        }
    }
    if let Some(min_occurrences) = min_entity_occurrences
        && min_occurrences > 1
    {
        let mut counts: BTreeMap<String, i64> = BTreeMap::new();
        for row in payloads {
            for entity_id in &row.entity_ids {
                if let Some(id) = entity_id.as_str() {
                    *counts.entry(id.to_string()).or_insert(0) += 1;
                }
            }
        }
        return entity_ids
            .iter()
            .filter(|id| counts.get(*id).copied().unwrap_or(0) >= min_occurrences)
            .cloned()
            .collect();
    }
    entity_ids.to_vec()
}

// ---------------------------------------------------------------------------
// Tool bodies
// ---------------------------------------------------------------------------

/// `list_source_ids`.
pub fn tool_list_source_ids(arguments: &Value) -> Result<Value, ToolError> {
    let limit_value = match arguments.get("limit") {
        None | Some(Value::Null) => 50i64,
        Some(value) => py_int(value)?,
    };
    let limit_val = limit_value.max(0) as usize;
    let project_id = optional_string(arguments.get("project_id"));
    let source_ids = graphstore::list_source_ids_graph(limit_val, project_id.as_deref())?;
    Ok(json!(source_ids))
}

/// `list_qdrant_collections`.
pub fn tool_list_qdrant_collections(arguments: &Value) -> Result<Value, ToolError> {
    let project_id = optional_string(arguments.get("project_id"));
    let backend = get_qdrant(project_id.as_deref())?;
    let names = qdrant::list_collection_names(&backend)?;
    if project_id.is_none() {
        return Ok(json!(names));
    }
    let expected = resolve_doc_collection(project_id.as_deref(), None);
    let filtered: Vec<String> = names
        .into_iter()
        .filter(|name| *name == expected)
        .collect();
    Ok(json!(filtered))
}

/// `semantic_search`.
pub fn tool_semantic_search(arguments: &Value) -> Result<Value, ToolError> {
    let query = coerce_query(arguments.get("query"));
    let top_k = match arguments.get("top_k") {
        None | Some(Value::Null) => 5i64,
        Some(value) => py_int(value)?,
    };
    let max_passage_chars = match arguments.get("max_passage_chars") {
        None | Some(Value::Null) => None,
        Some(value) => Some(py_int(value)?),
    };
    let include_entity_ids = coerce_bool(arguments.get("include_entity_ids"), true)?;
    let include_entity_mentions = coerce_bool(arguments.get("include_entity_mentions"), false)?;
    let source_id = optional_string(arguments.get("source_id"));
    let collection = optional_string(arguments.get("collection"));
    let project_id = optional_string(arguments.get("project_id"));

    let trace = cortex_embed::trace_enabled();
    let body_started = std::time::Instant::now();
    let q_vec = super::embed::encode_query(&query).map_err(ToolError::embed)?;
    let embed_ms = body_started.elapsed().as_secs_f64() * 1000.0;

    let search_started = std::time::Instant::now();
    let payloads = qdrant_search_entity_payload(
        &q_vec,
        top_k.max(0) as usize,
        source_id.as_deref(),
        collection.as_deref(),
        project_id.as_deref(),
    )?;
    let search_ms = search_started.elapsed().as_secs_f64() * 1000.0;

    let mut passages: Vec<Value> = Vec::new();
    for row in &payloads {
        let mut text = row.text.clone().unwrap_or_default();
        if let Some(max_chars) = max_passage_chars {
            text = truncate_chars(&text, max_chars.max(0) as usize);
        }
        let mut passage = Map::new();
        passage.insert("text".to_string(), json!(text));
        passage.insert(
            "score".to_string(),
            serde_json::Number::from_f64(row.score).map(Value::Number).unwrap_or(Value::Null),
        );
        passage.insert(
            "source_id".to_string(),
            row.source_id.clone().map(Value::String).unwrap_or(Value::Null),
        );
        passage.insert(
            "paragraph_id".to_string(),
            row.paragraph_id.clone().unwrap_or(Value::Null),
        );
        if include_entity_ids {
            passage.insert("entity_ids".to_string(), json!(row.entity_ids));
        }
        if include_entity_mentions {
            passage.insert("entity_mentions".to_string(), json!(row.entity_mentions));
        }
        passages.push(Value::Object(passage));
    }

    if trace {
        eprintln!(
            "[mind.tools.trace] embed={embed_ms:.1}ms qdrant={search_ms:.1}ms \
             body={:.1}ms",
            body_started.elapsed().as_secs_f64() * 1000.0
        );
    }
    Ok(json!({
        "query": query,
        "top_k": top_k,
        "source_id": source_id,
        "collection": resolve_doc_collection(project_id.as_deref(), collection.as_deref()),
        "collections_searched": resolve_doc_collections(project_id.as_deref(), collection.as_deref()),
        "passages": passages,
    }))
}

/// Python slice `text[:max_chars]` — UTF-8 aware (code points).
fn truncate_chars(text: &str, max_chars: usize) -> String {
    text.chars().take(max_chars).collect()
}

/// `DEFAULT_ENTITY_TYPES` of `mcp_graph_rag.py`.
pub const ENTITY_TYPES_DEFAULT: [&str; 13] = [
    "ORG", "PRODUCT", "STANDARD", "TECH", "CRYPTO", "SECURITY", "PROTOCOL", "VEHICLE", "DEVICE",
    "SERVER", "APP", "CERTIFICATE", "KEY",
];

/// `query_graph_rag_langextract`.
pub fn tool_query_graph_rag(arguments: &Value) -> Result<Value, ToolError> {
    let query = coerce_query(arguments.get("query"));
    let top_k = match arguments.get("top_k") {
        None | Some(Value::Null) => 5i64,
        Some(value) => py_int(value)?,
    };
    let related_k = match arguments.get("related_k") {
        None | Some(Value::Null) => 50i64,
        Some(value) => py_int(value)?,
    };
    let graph_depth = match arguments.get("graph_depth") {
        None | Some(Value::Null) => 1i64,
        Some(value) => py_int(value)?,
    };
    let include_entities = coerce_bool(arguments.get("include_entities"), true)?;
    let include_relations = coerce_bool(arguments.get("include_relations"), true)?;
    let expand_related = coerce_bool(arguments.get("expand_related"), true)?;
    let rerank = coerce_bool(arguments.get("rerank"), false)?;
    let rerank_entity_weight = match arguments.get("rerank_entity_weight") {
        None | Some(Value::Null) => 0.05f64,
        Some(value) => py_float(value)?,
    };
    let rerank_type_weight = match arguments.get("rerank_type_weight") {
        None | Some(Value::Null) => 0.1f64,
        Some(value) => py_float(value)?,
    };
    let rerank_confidence_weight = match arguments.get("rerank_confidence_weight") {
        None | Some(Value::Null) => 0.3f64,
        Some(value) => py_float(value)?,
    };
    let rerank_length_penalty = match arguments.get("rerank_length_penalty") {
        None | Some(Value::Null) => 0.0002f64,
        Some(value) => py_float(value)?,
    };
    let max_passage_chars = match arguments.get("max_passage_chars") {
        None | Some(Value::Null) => None,
        Some(value) => Some(py_int(value)?),
    };
    let min_score_to_expand = match arguments.get("min_score_to_expand") {
        None | Some(Value::Null) => None,
        Some(value) => Some(py_float(value)?),
    };
    let min_entity_occurrences = match arguments.get("min_entity_occurrences") {
        None | Some(Value::Null) => None,
        Some(value) => Some(py_int(value)?),
    };
    let source_id = optional_string(arguments.get("source_id"));
    let collection = optional_string(arguments.get("collection"));
    let project_id = optional_string(arguments.get("project_id"));

    let q_vec = super::embed::encode_query(&query).map_err(ToolError::embed)?;

    // entity_types coercion.
    let entity_types: Vec<Value> = match arguments.get("entity_types") {
        None | Some(Value::Null) => ENTITY_TYPES_DEFAULT.iter().map(|t| json!(t)).collect(),
        Some(Value::String(text)) => text
            .split(',')
            .map(str::trim)
            .filter(|item| !item.is_empty())
            .map(|item| json!(item))
            .collect(),
        Some(Value::Array(items)) => items.clone(),
        Some(other) => vec![other.clone()],
    };

    let payloads = qdrant_search_entity_payload(
        &q_vec,
        top_k.max(0) as usize,
        source_id.as_deref(),
        collection.as_deref(),
        project_id.as_deref(),
    )?;

    let mut passages: Vec<Value> = Vec::new();
    let mut entity_ids: Vec<String> = Vec::new();
    for row in &payloads {
        let mut text = row.text.clone().unwrap_or_default();
        if let Some(max_chars) = max_passage_chars {
            text = truncate_chars(&text, max_chars.max(0) as usize);
        }
        let mut passage = Map::new();
        passage.insert("text".to_string(), json!(text));
        passage.insert(
            "score".to_string(),
            serde_json::Number::from_f64(row.score).map(Value::Number).unwrap_or(Value::Null),
        );
        passage.insert(
            "source_id".to_string(),
            row.source_id.clone().map(Value::String).unwrap_or(Value::Null),
        );
        passage.insert(
            "paragraph_id".to_string(),
            row.paragraph_id.clone().unwrap_or(Value::Null),
        );
        passage.insert("_entity_mentions".to_string(), json!(row.entity_mentions));
        passages.push(Value::Object(passage));
        for id in &row.entity_ids {
            if let Some(id) = id.as_str()
                && !entity_ids.contains(&id.to_string())
            {
                entity_ids.push(id.to_string());
            }
        }
    }

    let entity_ids =
        filter_entity_ids_for_expansion(&entity_ids, &payloads, min_score_to_expand, min_entity_occurrences);

    let entities: Vec<Value> = if include_entities {
        graphstore::fetch_entities_by_ids(&entity_ids, project_id.as_deref())
            .map_err(ToolError::from)?
            .into_iter()
            .map(Value::Object)
            .collect()
    } else {
        Vec::new()
    };
    let mut relations: Vec<Value> = Vec::new();
    if include_relations && expand_related {
        let rows = graphstore::fetch_relations_with_depth(
            &entity_ids,
            &entity_types,
            related_k.max(0) as usize,
            graph_depth.max(0) as usize,
            project_id.as_deref(),
        )
        .map_err(ToolError::from)?;
        relations = rows.into_iter().map(Value::Object).collect();
    }

    if rerank {
        apply_heuristic_rerank(
            &mut passages,
            &entity_types,
            rerank_entity_weight,
            rerank_type_weight,
            rerank_confidence_weight,
            rerank_length_penalty,
        );
    }
    for passage in &mut passages {
        if let Some(object) = passage.as_object_mut() {
            object.remove("_entity_mentions");
        }
    }

    Ok(json!({
        "query": query,
        "top_k": top_k,
        "source_id": source_id,
        "collection": resolve_doc_collection(project_id.as_deref(), collection.as_deref()),
        "collections_searched": resolve_doc_collections(project_id.as_deref(), collection.as_deref()),
        "graph_depth": graph_depth,
        "min_score_to_expand": min_score_to_expand
            .map(|value| serde_json::Number::from_f64(value).map(Value::Number).unwrap_or(Value::Null))
            .unwrap_or(Value::Null),
        "min_entity_occurrences": min_entity_occurrences
            .map(|value| json!(value))
            .unwrap_or(Value::Null),
        "rerank_applied": rerank,
        "rerank_strategy": if rerank { json!("heuristic") } else { Value::Null },
        "passages": passages,
        "entities": entities,
        "relations": relations,
    }))
}

/// `get_paragraph_text`.
pub fn tool_get_paragraph_text(arguments: &Value) -> Result<Value, ToolError> {
    let source_id = match arguments.get("source_id") {
        None | Some(Value::Null) => None,
        Some(value) => match value {
            Value::String(text) if text.is_empty() => None,
            Value::String(text) => Some(text.clone()),
            other => Some(python_repr(other)),
        },
    };
    let paragraph_id = match arguments.get("paragraph_id") {
        None | Some(Value::Null) => 0i64,
        Some(value) => py_int(value)?,
    };
    let project_id = optional_string(arguments.get("project_id"));

    let Some(source_id) = source_id else {
        return Err(ToolError::value_error("source_id is required."));
    };
    let record = graphstore::fetch_paragraph_by_source(
        &source_id,
        paragraph_id,
        project_id.as_deref(),
    )
    .map_err(ToolError::from)?;
    match record {
        None => Ok(json!({
            "source_id": source_id,
            "paragraph_id": paragraph_id,
            "text": Value::Null,
            "found": false,
        })),
        Some(record) => {
            // Python `{**record, "found": True}`.
            let mut payload = record;
            payload.insert("found".to_string(), json!(true));
            Ok(Value::Object(payload))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn coerce_bool_matches_python() {
        assert!(matches!(coerce_bool(Some(&json!("true")), false), Ok(true)));
        assert!(matches!(coerce_bool(Some(&json!("ON")), false), Ok(true)));
        assert!(matches!(coerce_bool(Some(&json!(1)), false), Ok(true)));
        assert!(matches!(coerce_bool(Some(&json!("off")), true), Ok(false)));
        assert!(matches!(coerce_bool(Some(&json!("")), true), Ok(false)));
        assert!(matches!(coerce_bool(Some(&json!(0)), true), Ok(false)));
        assert!(matches!(coerce_bool(None, true), Ok(true)));
        let error = coerce_bool(Some(&json!("maybe")), false).unwrap_err();
        assert_eq!(error.message, "invalid boolean value: 'maybe'");
    }

    #[test]
    fn py_int_matches_python() {
        assert!(matches!(py_int(&json!("5")), Ok(5)));
        assert!(matches!(py_int(&json!(5.9)), Ok(5)));
        assert!(matches!(py_int(&json!(true)), Ok(1)));
        let error = py_int(&json!("abc")).unwrap_err();
        assert_eq!(
            error.message,
            "invalid literal for int() with base 10: 'abc'"
        );
    }

    #[test]
    fn heuristic_rerank_is_pure_math() {
        let mut passage = Map::new();
        passage.insert("score".to_string(), json!(0.5));
        passage.insert("text".to_string(), json!("abcd"));
        passage.insert(
            "_entity_mentions".to_string(),
            json!([
                {"id": "e1", "type": "ORG", "confidence": 0.8},
                {"id": "e1", "type": "TECH", "confidence": 0.6},
                {"name": "e2", "type": "ORG"}
            ]),
        );
        let entity_types: Vec<Value> = ["ORG", "TECH"].iter().map(|t| json!(t)).collect();
        let score = compute_heuristic_rerank_score(&passage, &entity_types, 0.05, 0.1, 0.3, 0.0002);
        // 0.5 + 0.05*2 + 0.1*3 + 0.3*(0.8+0.6)/2 - 0.0002*4
        let expected = 0.5 + 0.05 * 2.0 + 0.1 * 3.0 + 0.3 * 0.7 - 0.0002 * 4.0;
        assert!((score - expected).abs() < 1e-12, "{score} vs {expected}");
    }

    #[test]
    fn resolve_doc_collections_convention() {
        // No config in CWD fallback dir during unit tests — the naming
        // convention path is asserted.
        let collections = resolve_doc_collections(Some("zz_unregistered_p13"), None);
        assert_eq!(collections, vec!["zz_unregistered_p13_doc".to_string()]);
        assert_eq!(resolve_doc_collections(Some("x"), Some("custom")), vec!["custom".to_string()]);
    }

    #[test]
    fn expansion_filter_semantics() {
        let payloads = vec![PayloadRow {
            score: 0.9,
            text: None,
            source_id: None,
            paragraph_id: None,
            project_id: None,
            project_id_normalized: None,
            entity_ids: vec![json!("a"), json!("a"), json!("b")],
            entity_mentions: Vec::new(),
            collection: "c".to_string(),
        }];
        let ids = vec!["a".to_string(), "b".to_string(), "c".to_string()];
        // min_entity_occurrences=2 keeps only "a".
        assert_eq!(
            filter_entity_ids_for_expansion(&ids, &payloads, None, Some(2)),
            vec!["a".to_string()]
        );
        // Score gate: max score below threshold → no expansion.
        assert!(filter_entity_ids_for_expansion(&ids, &payloads, Some(0.95), None).is_empty());
        assert_eq!(
            filter_entity_ids_for_expansion(&ids, &payloads, Some(0.5), None),
            ids
        );
    }

    #[test]
    fn get_paragraph_text_requires_source() {
        let error = tool_get_paragraph_text(&json!({})).unwrap_err();
        assert_eq!(error.exception, "ValueError");
        assert_eq!(error.message, "source_id is required.");
    }
}

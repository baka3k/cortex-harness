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
//! Phase-12 graph tools — port của `code-tiny/mcp` backend layer.
//!
//! * `cplus_mcp.py` + `android_mcp.py` tool bodies (search/traversal/annotate/
//!   ipc/databases) → [`tools_search`] + [`tools_traversal`] (backend-keyed:
//!   cùng topology with Python unified server — `BACKENDS = {android, cplus}`
//!   được nạp in-process, dispatch theo parser).
//! * `unified_mcp.py` direct tools (explore_graph, semantic_search,
//!   project-context, bridge, workflow tools, reconstruct_flow) →
//!   [`tools_explore`], [`tools_semantic`], [`tools_bridge`],
//!   [`tools_workflows`], [`tools_flow`].
//! * `services/flow_reconstructor.py` → [`flow_reconstruct`].
//!
//! Python-plane (không port, documented): embedder (sentence-transformers
//! jina-embeddings-v3) — vector lane trả kết quả rỗng giống Python khi
//! collection/local-store không khớp scope fixture; LLM/langextract của
//! query_graph_rag_langextract.

pub mod flow_reconstruct;
pub mod runtime;
pub mod tools_bridge;
pub mod tools_explore;
pub mod tools_search;
pub mod tools_semantic;
pub mod tools_traversal;
pub mod tools_workflows;
pub mod vector_lane;

use std::collections::{BTreeMap, BTreeSet};

use cortex_graph_writer::project_scope::{
    prepare_project_scope_parameters, project_id_lookup_key,
};
use cortex_falkordb::client::Param;
use serde_json::{json, Map, Value};

use crate::framework_registry::{
    backend_label_union, capability_for_parser, default_relationships, query_engine_for_backend,
    searchable_labels, text_search_properties,
};
use crate::project_registry::{
    list_registered_projects, resolve_project_scope_candidates, resolve_project_targets,
};
use runtime::{json_to_param, with_graph_runtime};

pub use runtime::normalize_db_name;

/// Legacy search labels (cplus backend, giữ cho unscoped direct calls).
pub const LEGACY_SEARCH_LABELS: [&str; 9] = [
    "Function", "Type", "Namespace", "Field", "Alias", "Template", "FunctionType", "Event",
    "Resource",
];
pub const LEGACY_SEARCH_FRAMEWORKS: [&str; 3] = ["spring", "servlet_jsp", "mybatis"];
pub const NON_SYMBOL_SEARCH_LABELS: [&str; 10] = [
    "BuildConfiguration",
    "BuildDescriptor",
    "CallSite",
    "Dependency",
    "File",
    "FrameworkInstance",
    "Project",
    "ProjectModule",
    "Repository",
    "SemanticCoverage",
];

/// Android label predicate base set (`_android_symbol_labels`).
pub const ANDROID_BASE_LABELS: [&str; 15] = [
    "Function",
    "Class",
    "Type",
    "Namespace",
    "Package",
    "File",
    "AndroidManifest",
    "AndroidComponent",
    "AndroidResource",
    "GradleModule",
    "GradleDependency",
    "AndroidAnnotation",
    "AndroidNavRoute",
    "AndroidIntentAction",
    "AndroidHandlerMessage",
];

/// `_relationship_pattern(relationships, fallback)`.
pub fn relationship_pattern(relationships: &[String], fallback: &str) -> String {
    if relationships.is_empty() {
        return fallback.to_string();
    }
    let mut seen = BTreeSet::new();
    let mut parts: Vec<String> = Vec::new();
    for relationship in relationships {
        if seen.insert(relationship.clone()) {
            parts.push(relationship.clone());
        }
    }
    parts.join("|")
}

/// `_servlet_active_generation_predicate` (framework_registry) — Rust registry
/// exports it as a function of the variable name.
pub fn servlet_predicate(variable: &str) -> String {
    crate::framework_registry::servlet_active_generation_predicate(variable)
}

/// Backend identity for the fanout dispatch (`BACKENDS` in unified_mcp).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Backend {
    Android,
    Cplus,
}

pub const ALL_BACKENDS: [Backend; 2] = [Backend::Android, Backend::Cplus];

impl Backend {
    pub fn name(self) -> &'static str {
        match self {
            Backend::Android => "android",
            Backend::Cplus => "cplus",
        }
    }

    /// `_search_label_predicate` variants per backend.
    pub fn search_label_predicate(self, variable: &str, fanout: bool) -> String {
        let mut labels: Vec<String> = match self {
            Backend::Cplus => {
                let profile: Vec<String> = if fanout {
                    let mut union = backend_label_union(Some("cplus"));
                    union.extend(LEGACY_SEARCH_LABELS.iter().map(|label| label.to_string()));
                    union.sort();
                    union.dedup();
                    union
                } else {
                    LEGACY_SEARCH_LABELS.iter().map(|label| label.to_string()).collect()
                };
                profile
            }
            Backend::Android => {
                let mut base: Vec<String> =
                    ANDROID_BASE_LABELS.iter().map(|label| label.to_string()).collect();
                if fanout {
                    base.extend(backend_label_union(Some("android")));
                    base.sort();
                    base.dedup();
                }
                base
            }
        };
        labels.dedup();
        let mut clauses: Vec<String> =
            labels.iter().map(|label| format!("{variable}:{label}")).collect();
        if self == Backend::Cplus {
            let frameworks = LEGACY_SEARCH_FRAMEWORKS
                .iter()
                .map(|name| format!("'{name}'"))
                .collect::<Vec<_>>()
                .join(", ");
            clauses.push(format!("{variable}.framework IN [{frameworks}]"));
        }
        format!("({})", clauses.join(" OR "))
    }
}

/// Backend label predicate used by search-ish tools with a parser profile.
pub fn profile_search_labels(capability_name: Option<&str>) -> Vec<String> {
    match capability_name {
        Some(name) => searchable_labels(Some(name))
            .into_iter()
            .filter(|label| !NON_SYMBOL_SEARCH_LABELS.contains(&label.as_str()))
            .collect(),
        None => Vec::new(),
    }
}

pub fn profile_text_properties(capability_name: Option<&str>) -> Vec<String> {
    match capability_name {
        Some(name) => text_search_properties(Some(name)),
        None => Vec::new(),
    }
}

// ---------------------------------------------------------------------------
// Payload helpers
// ---------------------------------------------------------------------------

pub fn payload_str<'a>(payload: &'a Value, key: &str) -> Option<&'a str> {
    payload.get(key).and_then(Value::as_str)
}

pub fn payload_string(payload: &Value, key: &str) -> Option<String> {
    payload.get(key).and_then(Value::as_str).map(str::to_string)
}

pub fn payload_bool(payload: &Value, key: &str) -> bool {
    payload.get(key).and_then(Value::as_bool).unwrap_or(false)
}

/// Backend `_normalize_string_list` (khác unified variant: list rỗng thay vì None).
pub fn backend_string_list(value: Option<&Value>) -> Vec<String> {
    match value {
        None => Vec::new(),
        Some(Value::Null) => Vec::new(),
        Some(Value::String(text)) => {
            let trimmed = text.trim();
            if trimmed.is_empty() {
                return Vec::new();
            }
            if trimmed.contains(',') || trimmed.contains(';') {
                trimmed
                    .replace(';', ",")
                    .split(',')
                    .map(|part| part.trim().to_string())
                    .filter(|part| !part.is_empty())
                    .collect()
            } else {
                vec![trimmed.to_string()]
            }
        }
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(|item| match item {
                Value::String(text) => Some(text.trim().to_string()),
                Value::Number(number) => Some(number.to_string()),
                Value::Bool(flag) => Some(flag.to_string()),
                _ => None,
            })
            .filter(|item| !item.is_empty())
            .collect(),
        Some(Value::Object(object)) => object
            .get("value")
            .map(value_json_to_string_list)
            .unwrap_or_default(),
        Some(other) => {
            let text = value_scalar_to_string(other);
            if text.is_empty() {
                Vec::new()
            } else {
                vec![text]
            }
        }
    }
}

fn value_scalar_to_string(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Number(number) => number.to_string(),
        Value::Bool(flag) => flag.to_string(),
        _ => String::new(),
    }
}

fn value_json_to_string_list(value: &Value) -> Vec<String> {
    match value {
        Value::String(text) => vec![text.clone()],
        Value::Number(number) => vec![number.to_string()],
        Value::Bool(flag) => vec![flag.to_string()],
        Value::Array(items) => items.iter().filter_map(value_scalar_opt).collect(),
        _ => Vec::new(),
    }
}

fn value_scalar_opt(value: &Value) -> Option<String> {
    match value {
        Value::String(text) => Some(text.clone()),
        Value::Number(number) => Some(number.to_string()),
        Value::Bool(flag) => Some(flag.to_string()),
        _ => None,
    }
}

/// Backend `_normalize_rel_types` — uppercase, validate identifier.
pub fn normalize_rel_types(
    value: Option<&Value>,
    default: Option<Vec<String>>,
) -> Result<Vec<String>, String> {
    let items: Vec<String> = match value {
        None => default.unwrap_or_default(),
        Some(Value::Null) => default.unwrap_or_default(),
        Some(Value::String(text)) => {
            let trimmed = text.trim();
            if trimmed.is_empty() {
                Vec::new()
            } else if trimmed.contains(',') || trimmed.contains(';') {
                trimmed
                    .replace(';', ",")
                    .split(',')
                    .map(|part| part.trim().to_string())
                    .filter(|part| !part.is_empty())
                    .collect()
            } else {
                vec![trimmed.to_string()]
            }
        }
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(|item| match item {
                Value::String(text) => Some(text.trim().to_string()),
                Value::Number(number) => Some(number.to_string()),
                Value::Bool(flag) => Some(flag.to_string()),
                _ => None,
            })
            .filter(|item| !item.is_empty())
            .collect(),
        Some(Value::Bool(flag)) => vec![flag.to_string()],
        Some(Value::Number(number)) => vec![number.to_string()],
        Some(Value::Object(object)) => match object.get("value") {
            Some(Value::String(text)) => vec![text.trim().to_string()],
            Some(Value::Number(number)) => vec![number.to_string()],
            Some(Value::Bool(flag)) => vec![flag.to_string()],
            _ => Vec::new(),
        },
    };
    let mut cleaned: Vec<String> = Vec::new();
    for item in items {
        let upper = item.to_uppercase();
        if !upper.chars().all(|ch| ch.is_alphanumeric() || ch == '_') {
            return Err(format!("Invalid relationship type: {item}"));
        }
        cleaned.push(upper);
    }
    Ok(cleaned)
}

/// `_normalize_depth`.
pub fn normalize_depth(value: Option<&Value>, default: i64, max_limit: i64) -> i64 {
    let mut depth = match value {
        Some(Value::Number(number)) => number.as_i64().unwrap_or(default),
        Some(Value::String(text)) => text.trim().parse().unwrap_or(default),
        Some(Value::Bool(flag)) => {
            if *flag {
                1
            } else {
                default
            }
        }
        _ => default,
    };
    if depth < 1 {
        depth = 1;
    }
    if depth > max_limit {
        depth = max_limit;
    }
    depth
}

/// `_normalize_content_mode`.
pub fn normalize_content_mode(value: Option<&str>) -> String {
    match value.map(str::trim).filter(|text| !text.is_empty()) {
        None => "auto".to_string(),
        Some(mode) => {
            let lowered = mode.to_lowercase();
            if ["auto", "summary", "comment", "code", "name"].contains(&lowered.as_str()) {
                lowered
            } else {
                "auto".to_string()
            }
        }
    }
}

/// `_build_rel_match`.
pub fn build_rel_match(rel_types: &[String], depth: i64, direction: &str) -> String {
    let rel_token = if rel_types.is_empty() {
        String::new()
    } else {
        format!(":{}", rel_types.join("|"))
    };
    match direction {
        "in" | "incoming" => format!("<-[{rel_token}*1..{depth}]-"),
        "both" | "any" | "undirected" => format!("-[{rel_token}*1..{depth}]-"),
        _ => format!("-[{rel_token}*1..{depth}]->"),
    }
}

/// `_get_default_flow_rel_types`.
pub fn default_flow_rel_types(parser_type: Option<&str>) -> Vec<String> {
    let parser = parser_type.unwrap_or_default().trim().to_lowercase();
    if parser.is_empty() {
        return default_relationships(Some("cplus"), None);
    }
    match capability_for_parser(Some(&parser)) {
        Some(capability) => default_relationships(Some(capability.name.as_str()), None),
        None => vec![
            "CALLS".to_string(),
            "DECLARES".to_string(),
            "CONTAINS".to_string(),
            "DEPENDS_ON".to_string(),
        ],
    }
}

// ---------------------------------------------------------------------------
// Database candidates + cypher helpers
// ---------------------------------------------------------------------------

/// `cplus_mcp._resolve_db_candidates`.
pub fn resolve_db_candidates(project_id: Option<&str>) -> Result<Vec<String>, String> {
    let mut candidates: Vec<String> = Vec::new();
    let raw = project_id.unwrap_or("").trim();
    if !raw.is_empty() {
        let matched = resolve_project_scope_candidates(Some(raw), None)
            .map_err(|error| error_message(&error))?;
        for targets in &matched {
            let graph_name = normalize_db_name(&targets.code_graph);
            if !graph_name.is_empty() && !candidates.contains(&graph_name) {
                candidates.push(graph_name);
            }
        }
        if candidates.is_empty() {
            candidates.push(raw.to_string());
        }
    } else {
        let registered = list_registered_projects(None).map_err(|error| error_message(&error))?;
        for project in &registered {
            let targets = resolve_project_targets(Some(project), None)
                .map_err(|error| error_message(&error))?;
            let graph_name = normalize_db_name(&targets.code_graph);
            if !graph_name.is_empty() && !candidates.contains(&graph_name) {
                candidates.push(graph_name);
            }
        }
        let default_db = normalize_db_name(&runtime_default_graph());
        if !default_db.is_empty() && !candidates.contains(&default_db) {
            candidates.push(default_db);
        }
    }
    Ok(candidates)
}

fn error_message(error: &crate::project_registry::ProjectRegistryError) -> String {
    match error {
        crate::project_registry::ProjectRegistryError::NotRegistered(error) => error.message(),
        other => other.to_string(),
    }
}

/// Env default graph của backend Python (`DEFAULT_FALKORDB_GRAPH`).
pub fn runtime_default_graph() -> String {
    std::env::var("FALKORDB_GRAPH")
        .or_else(|_| std::env::var("FALKORDB_DATABASE"))
        .unwrap_or_else(|_| "hyper_graph".to_string())
}

pub fn runtime_default_db_name() -> String {
    normalize_db_name(&runtime_default_graph())
}

/// `_run_cypher_first` — chuẩn bị params (project scope siblings), fanout
/// per-db cho unscoped queries, aggregate + dedup theo JSON marker.
pub fn run_cypher_first(
    runtime: &mut runtime::GraphRuntime,
    query: &str,
    params: &Map<String, Value>,
    dbs: &[String],
) -> Result<(Option<String>, Vec<Map<String, Value>>), String> {
    let mut prepared: BTreeMap<String, Param> = BTreeMap::new();
    let converted: BTreeMap<String, Value> = params.clone().into_iter().collect();
    for (key, value) in cortex_graph_writer::project_scope::prepare_project_scope_parameters(
        &converted,
    ) {
        prepared.insert(key, json_to_param(&value));
    }
    let requested: Vec<&String> = dbs.iter().filter(|db| !db.is_empty()).collect();
    let available = runtime.list_databases();
    let is_scoped = params
        .get("project_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .map(|text| !text.is_empty())
        .unwrap_or(false);

    let mut candidates: Vec<String>;
    if !available.is_empty() {
        if is_scoped {
            candidates = requested
                .iter()
                .filter(|db| available.contains(&***db))
                .map(|db| (*db).clone())
                .collect();
            if candidates.is_empty() {
                // Registry graph names can diverge from the physical graph
                // name inside a migrated Ladybug store (registry says the
                // project id; the `.lbug` file carries `hyper_graph`). The
                // python driver opens the file regardless of the requested
                // name — fall back to the store's own graphs likewise.
                candidates = available.clone();
            }
            if candidates.is_empty() {
                let default_db = runtime_default_db_name();
                return Err(format!(
                    "No database candidates available for scoped query. Database not found. \
                     Use list_databases to inspect available DBs and \
                     activate_project(database_name=...) to switch. Available: {available:?}. \
                     Default: {default_db}."
                ));
            }
        } else {
            candidates = requested
                .iter()
                .filter(|db| available.contains(&***db))
                .map(|db| (*db).clone())
                .collect();
            for db in &available {
                if !candidates.contains(db) {
                    candidates.push(db.clone());
                }
            }
        }
    } else {
        candidates = requested.iter().map(|db| (*db).clone()).collect();
    }

    let aggregate = candidates.len() > 1 && !is_scoped;
    let mut used_db: Option<String> = None;
    let mut merged: Vec<Map<String, Value>> = Vec::new();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut last_error: Option<String> = None;
    let mut database_not_found = false;
    for db in &candidates {
        match runtime.execute_query(query, &prepared, Some(db)) {
            Ok(result) => {
                if !aggregate {
                    return Ok((Some(db.clone()), result));
                }
                if used_db.is_none() {
                    used_db = Some(db.clone());
                }
                for record in result {
                    let marker = canonical_marker(&Value::Object(record.clone()));
                    if seen.insert(marker) {
                        merged.push(record);
                    }
                }
            }
            Err(error) => {
                if is_database_not_found_error(&error) {
                    database_not_found = true;
                    last_error = Some(error);
                    continue;
                }
                return Err(error);
            }
        }
    }
    if let Some(used) = used_db {
        let global_limit = params
            .get("limit")
            .and_then(|value| match value {
                Value::Number(number) => number.as_i64(),
                Value::String(text) => text.trim().parse::<i64>().ok(),
                _ => None,
            });
        if let Some(limit) = global_limit.filter(|limit| *limit >= 0) {
            merged.truncate(limit as usize);
        }
        return Ok((Some(used), merged));
    }
    if let Some(error) = last_error {
        if database_not_found {
            let default_db = runtime_default_db_name();
            return Err(format!(
                "Database not found. Use list_databases to inspect available DBs and \
                 activate_project(database_name=...) to switch. Available: {available:?}. \
                 Default: {default_db}."
            ));
        }
        return Err(error);
    }
    Err("No database candidates available".to_string())
}

/// `json.dumps(record, sort_keys=True, default=str, separators=(",", ":"))`.
pub fn canonical_marker(value: &Value) -> String {
    fn canonicalize(value: &Value) -> Value {
        match value {
            Value::Object(map) => {
                let mut sorted: BTreeMap<String, Value> = BTreeMap::new();
                for (key, item) in map {
                    sorted.insert(key.clone(), canonicalize(item));
                }
                Value::Object(sorted.into_iter().collect())
            }
            other => other.clone(),
        }
    }
    serde_json::to_string(&canonicalize(value)).unwrap_or_default()
}

/// `is_database_not_found_error` — message-shape probe trên driver error text.
pub fn is_database_not_found_error(message: &str) -> bool {
    let lowered = message.to_lowercase();
    lowered.contains("unknown graph")
        || lowered.contains("database not found")
        || lowered.contains("no database candidates")
        || lowered.contains("graph not found")
        || lowered.contains("unknown database")
}

/// Chuẩn hoá error text của redis crate về dạng raw của server, trùng
/// `str(exc)` của redis-py (driver Python). rust-redis hiển thị server error
/// `"<kind> <detail>"` dạng `redis: "<kind>": <detail>`; redis-py giữ message
/// gốc — bridge tools nhét `str(exc)` vào `data.error` nên phải khớp bytes.
pub fn normalize_driver_error(message: &str) -> String {
    let Some(rest) = message.strip_prefix("redis: ") else {
        return message.to_string();
    };
    let Some(inner) = rest.strip_prefix('"') else {
        return rest.to_string();
    };
    match inner.split_once("\": ") {
        Some((kind, detail)) => format!("{kind} {detail}"),
        None => inner.to_string(),
    }
}

// ---------------------------------------------------------------------------
// Record builders (nodes/edges/paths)
// ---------------------------------------------------------------------------

/// `_fallback_node_name` (cplus ordering).
pub fn fallback_node_name(properties: &Map<String, Value>, node_id: Option<&str>) -> String {
    for key in ["name", "qualified_name", "type_signature", "target_name", "file_path", "path"] {
        if let Some(Value::String(text)) = properties.get(key)
            && !text.trim().is_empty()
        {
            return text.clone();
        }
    }
    node_id.unwrap_or_default().to_string()
}

/// `select_content_with_fallback` (summary_empty_fallback=True — cplus).
pub fn select_content(
    properties: &Map<String, Value>,
    node_id: Option<&str>,
    mode: &str,
    summary_empty_fallback: bool,
) -> String {
    let text_of = |key: &str| {
        properties
            .get(key)
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    };
    let summary = text_of("summary");
    let comment = text_of("comment");
    let code = text_of("code");
    match mode {
        "summary" => {
            if !summary.trim().is_empty() || !summary_empty_fallback {
                summary
            } else {
                fallback_node_name(properties, node_id)
            }
        }
        "comment" => comment,
        "code" => code,
        "name" => fallback_node_name(properties, node_id),
        _ => {
            if !summary.trim().is_empty() {
                summary
            } else if !comment.trim().is_empty() {
                comment
            } else if code.trim().is_empty() {
                preview_from_text(properties).unwrap_or_else(|| {
                    fallback_node_name(properties, node_id)
                })
            } else {
                fallback_node_name(properties, node_id)
            }
        }
    }
}

const PREVIEW_CHARS: usize = 240;

fn preview_from_text(properties: &Map<String, Value>) -> Option<String> {
    let text = properties.get("text").and_then(Value::as_str)?;
    if text.trim().is_empty() {
        return None;
    }
    if text.chars().count() <= PREVIEW_CHARS {
        return Some(text.to_string());
    }
    let truncated: String = text.chars().take(PREVIEW_CHARS).collect();
    Some(format!("{truncated}\u{2026}"))
}

/// Backend `_record_node` — `prunes_note` + prop-exclusion phân biệt backend
/// (cplus loại "id"/"labels"/"_graph_id"; android chỉ loại "labels").
pub fn record_node(
    node: &Value,
    mode: &str,
    include_raw_fields: bool,
    prunes_note: bool,
    backend: Backend,
) -> Value {
    let object = match node.as_object() {
        Some(object) => object,
        None => return json!({"id": Value::Null, "labels": [], "properties": {}}),
    };
    let node_id = object.get("id").cloned().unwrap_or(Value::Null);
    let excluded: &[&str] = match backend {
        Backend::Cplus => &["id", "labels", "_graph_id"],
        Backend::Android => &["labels"],
    };
    let mut props: Map<String, Value> = object
        .iter()
        .filter(|(key, _)| !excluded.contains(&key.as_str()))
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();
    let id_text = node_id.as_str().map(str::to_string);
    let content = select_content(&props, id_text.as_deref(), mode, true);
    if !include_raw_fields {
        props.remove("summary");
        props.remove("comment");
        props.remove("code");
        if prunes_note {
            props.remove("note");
        }
    }
    let labels = object.get("labels").cloned().unwrap_or(json!([]));
    props.insert("content_mode".to_string(), json!(mode));
    props.insert("content".to_string(), json!(content));
    json!({
        "id": node_id,
        "labels": labels,
        "properties": props,
    })
}

/// `_record_query_node` — row có "n" + optional "labels".
pub fn record_query_node(
    row: &Map<String, Value>,
    key: &str,
    mode: &str,
    include_raw_fields: bool,
    prunes_note: bool,
    backend: Backend,
) -> Value {
    let Some(node) = row.get(key) else {
        return Value::Null;
    };
    let mut node_value = node.clone();
    if let (Some(object), Some(labels)) = (node.as_object(), row.get("labels")) {
        if !labels.is_null() {
            let mut merged = object.clone();
            merged.insert("labels".to_string(), labels.clone());
            node_value = Value::Object(merged);
        }
    }
    record_node(&node_value, mode, include_raw_fields, prunes_note, backend)
}

/// `_record_rel` — dict-shaped rel từ driver normalize (`_type`, `_start_id`, `_end_id`).
pub fn record_rel(rel: &Value, graph_node_ids: Option<&Map<String, Value>>) -> Value {
    let Some(object) = rel.as_object() else {
        return json!({"type": Value::Null, "properties": {}, "start_id": Value::Null, "end_id": Value::Null});
    };
    let raw_start = object.get("start_id").or_else(|| object.get("_start_id")).cloned();
    let raw_end = object.get("end_id").or_else(|| object.get("_end_id")).cloned();
    let endpoint = |value: Option<Value>| -> Value {
        match value {
            Some(Value::Object(inner)) => {
                let id = inner.get("id").cloned().unwrap_or(Value::Null);
                if let (Some(map), Some(id_text)) = (graph_node_ids, id.as_str()) {
                    map.get(id_text).cloned().unwrap_or(id)
                } else {
                    id
                }
            }
            Some(other) => other,
            None => Value::Null,
        }
    };
    let properties = match object.get("properties") {
        Some(props) if props.is_object() => props.clone(),
        _ => Value::Object(
            object
                .iter()
                .filter(|(key, _)| {
                    !matches!(
                        key.as_str(),
                        "type" | "_type" | "start_id" | "_start_id" | "end_id" | "_end_id"
                    )
                })
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect(),
        ),
    };
    let rel_type = object
        .get("type")
        .or_else(|| object.get("_type"))
        .cloned()
        .unwrap_or(Value::Null);
    json!({
        "type": rel_type,
        "properties": properties,
        "start_id": endpoint(raw_start),
        "end_id": endpoint(raw_end),
    })
}

/// `_paths_to_graph` — driver FalkorDB normalize Path thành
/// `{"nodes": [...], "edges": [...]}` (mỗi node dict có `_graph_id`).
pub fn paths_to_graph(
    paths: &[Value],
    mode: &str,
    include_raw_fields: bool,
    prunes_note: bool,
    backend: Backend,
) -> Value {
    let mut nodes_index: BTreeMap<String, Value> = BTreeMap::new();
    let mut node_order: Vec<String> = Vec::new();
    let mut edges: Vec<Value> = Vec::new();
    for path in paths {
        let Some(object) = path.as_object() else { continue };
        let path_nodes = object.get("nodes").and_then(Value::as_array).cloned().unwrap_or_default();
        let path_rels = object
            .get("edges")
            .or_else(|| object.get("relationships"))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut graph_node_ids: Map<String, Value> = Map::new();
        for node in &path_nodes {
            let Some(node_object) = node.as_object() else { continue };
            let node_id = node_object.get("id").and_then(Value::as_str).map(str::to_string);
            if let Some(node_id) = &node_id {
                let graph_id = node_object.get("_graph_id").cloned();
                if let Some(graph_id) = graph_id.filter(|value| !value.is_null()) {
                    graph_node_ids.insert(graph_id.to_string(), json!(node_id));
                }
            }
            if let Some(node_id) = &node_id
                && !nodes_index.contains_key(node_id)
            {
                nodes_index.insert(node_id.clone(), record_node(node, mode, include_raw_fields, prunes_note, backend));
                node_order.push(node_id.clone());
            }
        }
        for rel in &path_rels {
            edges.push(record_rel(rel, Some(&graph_node_ids)));
        }
    }
    let nodes: Vec<Value> = node_order
        .into_iter()
        .filter_map(|id| nodes_index.remove(&id))
        .collect();
    json!({"nodes": nodes, "edges": edges})
}

// ---------------------------------------------------------------------------
// Relationship resolution with diagnostics
// ---------------------------------------------------------------------------

pub struct ResolvedRelationships {
    pub applied: Vec<String>,
    pub diagnostics: Value,
}

/// `_resolve_rel_types_with_diagnostics`.
pub fn resolve_rel_types_with_diagnostics(
    runtime: &mut runtime::GraphRuntime,
    rel_types_input: Option<&Value>,
    parser_type: Option<&str>,
    db_candidates: &[String],
    explicit: bool,
) -> Result<ResolvedRelationships, String> {
    let default_rels = default_flow_rel_types(parser_type);
    let requested = normalize_rel_types(rel_types_input, Some(default_rels))?;
    let available = runtime::list_relationship_types(runtime, db_candidates);
    let Some(available) = available else {
        return Ok(ResolvedRelationships {
            applied: requested.clone(),
            diagnostics: json!({
                "schema_status": "unavailable",
                "requested_relationships": requested,
                "message": "Provider relationship schema could not be inspected; requested relationships were retained.",
            }),
        });
    };
    let available_set: BTreeSet<&String> = available.iter().collect();
    let used: Vec<String> = requested
        .iter()
        .filter(|item| available_set.contains(item))
        .cloned()
        .collect();
    let omitted: Vec<String> = requested
        .iter()
        .filter(|item| !available_set.contains(item))
        .cloned()
        .collect();
    let status = if omitted.is_empty() {
        "supported"
    } else if !used.is_empty() {
        "partial"
    } else {
        "unsupported"
    };
    let applied = if used.is_empty() { requested.clone() } else { used.clone() };
    let mut diagnostics = json!({
        "schema_status": "available",
        "support_status": status,
        "explicit_request": explicit,
    });
    if !omitted.is_empty() {
        let diagnostics_object = diagnostics.as_object_mut().expect("diagnostics object");
        diagnostics_object
            .insert("requested_relationships".to_string(), json!(requested));
        diagnostics_object
            .insert("available_relationships".to_string(), json!(available));
        if !used.is_empty() {
            diagnostics_object
                .insert("omitted_relationships".to_string(), json!(omitted));
        }
    }
    Ok(ResolvedRelationships {
        applied,
        diagnostics,
    })
}

/// `_unsupported_relationship_result`.
pub fn unsupported_relationship_result(parser_type: Option<&str>, diagnostics: &Value) -> Value {
    let parser = parser_type
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .unwrap_or("generic");
    let requested = diagnostics
        .get("requested_relationships")
        .cloned()
        .unwrap_or(json!([]));
    json!({
        "error": format!(
            "Parser '{parser}' requested relationships that are unavailable in the active provider: {}",
            requested
                .as_array()
                .map(|items| items
                    .iter()
                    .filter_map(Value::as_str)
                    .collect::<Vec<_>>()
                    .join(", "))
                .unwrap_or_default(),
        ),
        "error_type": "unsupported_capability",
        "capability_diagnostics": diagnostics,
    })
}

// ---------------------------------------------------------------------------
// Semantic coverage (call_evidence port)
// ---------------------------------------------------------------------------

pub const SERVED_SCHEMA_FINGERPRINT: &str = "8613fc08894a26c2";

const SCOPE_KEY_FIELDS: [&str; 6] = [
    "project_id",
    "generation_id",
    "revision",
    "policy_version",
    "tu_key",
    "config_fingerprint",
];

fn scope_key(record: &Map<String, Value>) -> Vec<String> {
    SCOPE_KEY_FIELDS
        .iter()
        .map(|field| {
            record
                .get(*field)
                .map(|value| match value {
                    Value::String(text) => text.clone(),
                    Value::Null => String::new(),
                    other => other.to_string(),
                })
                .unwrap_or_default()
        })
        .collect()
}

fn fingerprint_payload(value: &Value) -> String {
    use sha2::{Digest, Sha256};
    let canonical = serde_json::to_string(value).unwrap_or_default();
    let digest = Sha256::digest(canonical.as_bytes());
    hex(&digest)
}

fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

/// `frontier_coverage`.
pub fn frontier_coverage(records: &[Map<String, Value>]) -> Value {
    let mut counts: BTreeMap<String, i64> = BTreeMap::new();
    let mut reasons: Vec<Value> = Vec::new();
    for record in records {
        let status = record
            .get("status")
            .map(value_to_display_string)
            .unwrap_or_default();
        let detail = record
            .get("detail")
            .map(value_to_display_string)
            .unwrap_or_default();
        let identity = record
            .get("tu_key")
            .or_else(|| record.get("frontier"))
            .map(value_to_display_string)
            .unwrap_or_default();
        *counts.entry(status.clone()).or_insert(0) += 1;
        if status != "complete" {
            let mut reason = format!("{}: {status}", if identity.is_empty() { "frontier" } else { &identity });
            if !detail.is_empty() {
                reason.push_str(&format!(" ({detail})"));
            }
            reasons.push(json!(reason));
        }
    }
    if counts.is_empty() {
        return json!({
            "status": "unknown",
            "reasons": ["no semantic coverage records found for the visited frontier"],
            "counts": {},
            "record_count": 0,
        });
    }
    let total: i64 = counts.values().sum();
    let status = if counts.get("complete").copied().unwrap_or(0) == total {
        "complete"
    } else {
        "partial"
    };
    let counts_json = Value::Object(
        counts
            .into_iter()
            .map(|(key, value)| (key, json!(value)))
            .collect(),
    );
    json!({
        "status": status,
        "reasons": reasons,
        "counts": counts_json,
        "record_count": total,
    })
}

/// `exact_frontier_coverage` (semantic-coverage block cho query_subgraph).
pub fn exact_frontier_coverage(
    expected_keys: &[Map<String, Value>],
    actual_records: &[Map<String, Value>],
) -> Value {
    let expected: Vec<Vec<String>> = expected_keys.iter().map(scope_key).collect();
    if expected.is_empty() {
        return json!({
            "status": "unknown",
            "expected_key_count": 0,
            "actual_key_count": actual_records.len(),
            "reasons": ["semantic_scope_manifest_missing"],
        });
    }
    let mut expected_set: BTreeSet<Vec<String>> = BTreeSet::new();
    for key in &expected {
        expected_set.insert(key.clone());
    }
    let mut counts: BTreeMap<Vec<String>, i64> = BTreeMap::new();
    let mut actual_by_key: BTreeMap<Vec<String>, Vec<&Map<String, Value>>> = BTreeMap::new();
    for record in actual_records {
        let key = scope_key(record);
        *counts.entry(key.clone()).or_insert(0) += 1;
        actual_by_key.entry(key).or_default().push(record);
    }
    let actual_set: BTreeSet<Vec<String>> = actual_by_key.keys().cloned().collect();
    let missing: Vec<Vec<String>> = expected_set.difference(&actual_set).cloned().collect();
    let unexpected: Vec<Vec<String>> = actual_set.difference(&expected_set).cloned().collect();
    let duplicates: Vec<Vec<String>> = counts
        .iter()
        .filter(|(_, count)| **count != 1)
        .map(|(key, _)| key.clone())
        .collect();
    let incomplete: Vec<Vec<String>> = expected_set
        .intersection(&actual_set)
        .filter(|key| {
            let records = &actual_by_key[*key];
            records.len() != 1
                || records[0]
                    .get("status")
                    .map(value_to_display_string)
                    .unwrap_or_default()
                    != "complete"
        })
        .cloned()
        .collect();
    let mut reasons: Vec<&str> = Vec::new();
    if !missing.is_empty() {
        reasons.push("scope_keys_missing");
    }
    if !unexpected.is_empty() {
        reasons.push("scope_keys_unexpected");
    }
    if !duplicates.is_empty() {
        reasons.push("scope_keys_duplicate");
    }
    if !incomplete.is_empty() {
        reasons.push("scope_keys_incomplete");
    }
    let expected_json: Vec<Value> = expected_set
        .iter()
        .map(|key| json!(key))
        .collect();
    json!({
        "status": if reasons.is_empty() { "complete" } else { "partial" },
        "expected_key_count": expected_set.len(),
        "actual_key_count": actual_records.len(),
        "missing_key_count": missing.len(),
        "unexpected_key_count": unexpected.len(),
        "duplicate_key_count": duplicates.len(),
        "incomplete_key_count": incomplete.len(),
        "reasons": reasons,
        "scope_fingerprint": fingerprint_payload(&json!(expected_json)),
    })
}

fn value_to_display_string(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// `traversal_outcome`.
pub fn traversal_outcome(coverage_status: &str, result_is_empty: bool) -> &'static str {
    if result_is_empty {
        if coverage_status == "complete" {
            "complete"
        } else {
            "incomplete"
        }
    } else {
        "complete"
    }
}

/// `suggested_next_semantic_scope`.
pub fn suggested_next_semantic_scope(coverage_block: &Value) -> Value {
    let reasons = coverage_block
        .get("reasons")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let mut scopes: Vec<String> = Vec::new();
    for reason in reasons {
        let text = match reason {
            Value::String(text) => text,
            other => other.to_string(),
        };
        if let Some((prefix, _)) = text.split_once(": ") {
            scopes.push(prefix.to_string());
        }
    }
    scopes.truncate(10);
    if scopes.is_empty()
        && coverage_block.get("status").and_then(Value::as_str) != Some("complete")
    {
        scopes = vec!["run semantic analysis to produce coverage records".to_string()];
    }
    json!(scopes)
}

/// `_semantic_coverage_block` (cplus backend) — exact frontier coverage.
pub fn semantic_coverage_block(
    runtime: &mut runtime::GraphRuntime,
    dbs: &[String],
    project_id: Option<&str>,
) -> Value {
    let query = "\n        MATCH (scope:SemanticScopeManifestKey) \n        WHERE scope.project_id = $project_id OR $project_id IS NULL \n        OPTIONAL MATCH (coverage:SemanticCoverage) \n        WHERE coverage.project_id = scope.project_id \n        AND coverage.generation_id = scope.generation_id \n        AND coverage.revision = scope.revision \n        AND coverage.policy_version = scope.policy_version \n        AND coverage.tu_key = scope.tu_key \n        AND coverage.config_fingerprint = scope.config_fingerprint \n        RETURN scope.project_id AS project_id, \n               scope.generation_id AS generation_id, scope.revision AS revision, \n               scope.policy_version AS policy_version, scope.tu_key AS tu_key, \n               scope.config_fingerprint AS config_fingerprint, \n               coverage.status AS status, coverage.detail AS detail\n        ";
    let project_param = json!(project_id.unwrap_or("").trim().is_empty().then(|| Value::Null).map(|_| ()).map_or(Value::Null, |_| project_id.map(|id| json!(id)).unwrap_or(Value::Null)));
    let mut params: Map<String, Value> = Map::new();
    params.insert(
        "project_id".to_string(),
        match project_id {
            Some(id) if !id.trim().is_empty() => json!(id),
            _ => Value::Null,
        },
    );
    let _ = project_param;
    let mut records: Vec<Map<String, Value>> = Vec::new();
    for db in dbs {
        match run_cypher_first(runtime, query, &params, std::slice::from_ref(db)) {
            Ok((_, rows)) => records.extend(rows),
            Err(error) => {
                if is_database_not_found_error(&error) {
                    continue;
                }
            }
        }
    }
    let mut revisions: Vec<String> = Vec::new();
    for row in &records {
        if let Some(revision) = row.get("revision").map(value_to_display_string)
            && !revision.is_empty()
            && !revisions.contains(&revision)
        {
            revisions.push(revision);
        }
    }
    revisions.sort();
    let expected: Vec<Map<String, Value>> = records
        .iter()
        .map(|row| {
            let mut key = Map::new();
            for field in SCOPE_KEY_FIELDS {
                key.insert(field.to_string(), row.get(field).cloned().unwrap_or(Value::Null));
            }
            key
        })
        .collect();
    let actual: Vec<Map<String, Value>> = records
        .iter()
        .filter(|row| row.get("status").map(|value| !value.is_null()).unwrap_or(false))
        .cloned()
        .collect();
    let mut block = exact_frontier_coverage(&expected, &actual);
    let block_object = block.as_object_mut().expect("coverage block");
    block_object.insert(
        "served_revision".to_string(),
        revisions
            .last()
            .map(|revision| json!(revision))
            .unwrap_or(Value::Null),
    );
    block_object.insert(
        "served_schema_fingerprint".to_string(),
        json!(SERVED_SCHEMA_FINGERPRINT),
    );
    block_object.insert(
        "semantic_policy_version".to_string(),
        records
            .iter()
            .find_map(|row| row.get("policy_version"))
            .map(value_to_display_string)
            .filter(|value| !value.is_empty())
            .map(|value| json!(value))
            .unwrap_or(Value::Null),
    );
    block_object.insert("evidence_record_count".to_string(), json!(actual.len()));
    block
}

/// `_outcome_payload`.
pub fn outcome_payload(
    runtime: &mut runtime::GraphRuntime,
    dbs: &[String],
    project_id: Option<&str>,
    result_is_empty: bool,
    extra: Option<Map<String, Value>>,
) -> Value {
    let coverage = if dbs.is_empty() {
        frontier_coverage(&[])
    } else {
        semantic_coverage_block(runtime, dbs, project_id)
    };
    let status = coverage
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let outcome = traversal_outcome(&status, result_is_empty);
    let mut payload = Map::new();
    payload.insert("outcome".to_string(), json!(outcome));
    payload.insert("semantic_coverage".to_string(), coverage.clone());
    if outcome == "incomplete" {
        payload.insert(
            "suggested_next_semantic_scope".to_string(),
            suggested_next_semantic_scope(&coverage),
        );
        payload.insert(
            "reason".to_string(),
            json!("semantic frontier incomplete; negative conclusions are not authoritative"),
        );
    }
    if let Some(extra) = extra {
        for (key, value) in extra {
            payload.insert(key, value);
        }
    }
    Value::Object(payload)
}

// ---------------------------------------------------------------------------
// Direct-tool capability context (unified_mcp._resolve_direct_capability_context)
// ---------------------------------------------------------------------------

pub struct CapabilityContext {
    pub selected_parser: Option<String>,
    pub relationships: Vec<String>,
    pub routing: Value,
    pub diagnostics: Option<Value>,
    pub error: Option<Value>,
}

/// `_resolve_direct_capability_context`.
pub fn resolve_direct_capability_context(
    runtime: &mut runtime::GraphRuntime,
    tool_name: &str,
    parser_type: Option<&str>,
    db: Option<&str>,
    required_relationships: &[&str],
    required_labels: &[&str],
) -> Result<CapabilityContext, String> {
    let selected_parser = parser_type
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .map(|text| text.to_lowercase());
    let capability = capability_for_parser(selected_parser.as_deref());
    let backend_name: &str = match &capability {
        Some(capability) => capability.backend.as_str(),
        None => "cplus",
    };
    let routing = crate::dispatch::capability_summary(selected_parser.as_deref());
    if let Some(parser) = &selected_parser
        && capability.is_none()
    {
        let mut payload = Map::new();
        payload.insert("parser_type".to_string(), json!(parser));
        let error = crate::dispatch::unsupported_parser_result(tool_name, &Value::Object(payload), parser);
        return Ok(CapabilityContext {
            selected_parser,
            relationships: Vec::new(),
            routing,
            diagnostics: None,
            error: Some(error),
        });
    }
    let required: Vec<String> = required_relationships
        .iter()
        .map(|item| item.to_string())
        .collect();
    let labels_required: Vec<String> = required_labels.iter().map(|item| item.to_string()).collect();
    let default_rels: Vec<String> = match (&capability, backend_name) {
        (Some(capability), "android") => {
            let _ = capability;
            default_relationships(None, None)
        }
        (Some(capability), _) => default_relationships(Some(capability.name.as_str()), Some(tool_name)),
        (None, _) => {
            let mut rels: Vec<String> = default_relationships(None, None);
            for item in &required {
                if !rels.contains(item) {
                    rels.push(item.clone());
                }
            }
            rels
        }
    };
    let mut requested_rels = default_rels.clone();
    for item in &required {
        if !requested_rels.contains(item) {
            requested_rels.push(item.clone());
        }
    }
    let db_candidates = resolve_db_candidates(db)?;
    let resolved = resolve_rel_types_with_diagnostics(
        runtime,
        Some(&json!(requested_rels)),
        selected_parser.as_deref(),
        &db_candidates,
        false,
    )?;
    let relationships = resolved.applied;
    let mut diagnostics: Option<Value> = Some(resolved.diagnostics);
    let missing_required: Vec<String> = required
        .iter()
        .filter(|item| !relationships.contains(item))
        .cloned()
        .collect();
    let mut available_labels: Option<Vec<String>> = None;
    if !labels_required.is_empty() {
        available_labels = runtime::list_node_labels(runtime, &db_candidates);
    }
    let label_schema_available = available_labels.is_some();
    let missing_labels: Vec<String> = match &available_labels {
        Some(labels) => labels_required
            .iter()
            .filter(|label| !labels.contains(label))
            .cloned()
            .collect(),
        None => Vec::new(),
    };
    let mut warnings: Vec<String> = Vec::new();
    if !missing_required.is_empty() {
        warnings.push(format!(
            "Provider does not expose required relationships: {} — the query runs anyway and simply contributes no rows.",
            missing_required.join(", ")
        ));
    }
    if !labels_required.is_empty() && !label_schema_available {
        warnings.push("Provider label schema could not be inspected.".to_string());
    } else if !missing_labels.is_empty() {
        warnings.push(format!(
            "Provider does not expose required labels: {} — the query runs anyway and simply contributes no rows.",
            missing_labels.join(", ")
        ));
    }
    if let Some(diagnostics_value) = &mut diagnostics {
        let object = diagnostics_value.as_object_mut().expect("diagnostics object");
        if !required.is_empty() {
            object.insert("required_relationships".to_string(), json!(required));
            object.insert(
                "missing_required_relationships".to_string(),
                json!(missing_required),
            );
        }
        if !labels_required.is_empty() {
            object.insert("required_labels".to_string(), json!(labels_required));
            object.insert("missing_required_labels".to_string(), json!(missing_labels));
            object.insert(
                "label_schema_status".to_string(),
                json!(if label_schema_available { "available" } else { "unavailable" }),
            );
            if !missing_labels.is_empty() {
                object.insert(
                    "available_labels".to_string(),
                    json!(available_labels.clone().unwrap_or_default()),
                );
            }
        }
        if !warnings.is_empty() {
            object.insert("warnings".to_string(), json!(warnings));
        }
    } else if !warnings.is_empty() {
        diagnostics = Some(json!({"warnings": warnings}));
    }
    Ok(CapabilityContext {
        selected_parser,
        relationships,
        routing,
        diagnostics,
        error: None,
    })
}

// ---------------------------------------------------------------------------
// Fanout merge (unified_mcp._merge_fanout_results)
// ---------------------------------------------------------------------------

const FANOUT_LIST_RESULT_KEYS: [&str; 11] = [
    "results", "ids", "nodes", "edges", "paths", "symbols", "classes", "functions",
    "endpoint_paths", "workflows", "matches",
];
const FANOUT_SINGLE_RESULT_KEYS: [&str; 3] = ["node", "path", "endpoint"];
const FANOUT_DIAGNOSTIC_KEYS: [&str; 13] = [
    "db", "ok", "error", "query_engine", "capability", "capability_diagnostics", "reason",
    "rel_types", "direction", "max_depth", "support_status", "support_statuses",
    "matched_node_count",
];

fn dedup_key(key: &str, item: &Value) -> Option<Value> {
    if key == "ids" {
        let value = item.get("value").unwrap_or(item);
        if value.is_null() {
            return None;
        }
        return Some(json!(["id", value]));
    }
    if FANOUT_NODE_KEYS.contains(&key) {
        if let Some(object) = item.as_object()
            && let Some(id) = object.get("id")
        {
            return Some(json!(["id", id]));
        }
        return None;
    }
    if key == "edges" {
        if let Some(object) = item.as_object() {
            return Some(json!([
                "edge",
                object.get("start_id").cloned().unwrap_or(Value::Null),
                object.get("type").cloned().unwrap_or(Value::Null),
                object.get("end_id").cloned().unwrap_or(Value::Null),
            ]));
        }
        return None;
    }
    None
}

const FANOUT_NODE_KEYS: [&str; 5] = ["results", "nodes", "symbols", "classes", "functions"];

fn tag_fanout_items(parser: &str, items: &[Value]) -> Vec<Value> {
    items
        .iter()
        .map(|item| match item {
            Value::Object(map) => {
                let mut merged = Map::new();
                merged.insert("parser_type".to_string(), json!(parser));
                for (key, value) in map {
                    merged.insert(key.clone(), value.clone());
                }
                Value::Object(merged)
            }
            other => json!({"parser_type": parser, "value": other}),
        })
        .collect()
}

/// `_merge_fanout_results`.
pub fn merge_fanout_results(
    parser_results: &BTreeMap<String, Value>,
    parser_errors: &BTreeMap<String, Value>,
) -> Value {
    let successful_parsers: Vec<String> = parser_results
        .iter()
        .filter(|(_, result)| !matches!(result.get("ok"), Some(Value::Bool(false))))
        .map(|(parser, _)| parser.clone())
        .collect();
    let mut merged = Map::new();
    merged.insert("ok".to_string(), json!(true));
    merged.insert("parsers_searched".to_string(), json!(successful_parsers));
    merged.insert(
        "parsers_failed".to_string(),
        json!(parser_errors.keys().cloned().collect::<Vec<_>>()),
    );
    merged.insert(
        "parser_results".to_string(),
        Value::Object(parser_results.clone().into_iter().collect()),
    );
    merged.insert(
        "parser_errors".to_string(),
        Value::Object(parser_errors.clone().into_iter().collect()),
    );
    merged.insert("query_engine".to_string(), json!("graph_fanout"));

    // Union of list keys across successful parsers (insertion order per parser).
    let mut list_keys: Vec<String> = Vec::new();
    for parser in &successful_parsers {
        let Some(payload) = parser_results.get(parser).and_then(Value::as_object) else {
            continue;
        };
        for (key, value) in payload {
            if FANOUT_DIAGNOSTIC_KEYS.contains(&key.as_str())
                || FANOUT_SINGLE_RESULT_KEYS.contains(&key.as_str())
            {
                continue;
            }
            if value.is_array() && !list_keys.contains(key) {
                list_keys.push(key.clone());
            }
        }
    }

    let mut dedup_removed: i64 = 0;
    for key in &list_keys {
        let mut merged_items: Vec<Value> = Vec::new();
        let mut seen_keys: BTreeSet<String> = BTreeSet::new();
        for parser in &successful_parsers {
            let payload = parser_results.get(parser).cloned().unwrap_or(Value::Null);
            let value = payload.get(key).cloned().unwrap_or(Value::Null);
            let Some(items) = value.as_array() else { continue };
            if items.is_empty() {
                continue;
            }
            for tagged in tag_fanout_items(parser, items) {
                let Some(dedup) = dedup_key(key, &tagged) else {
                    merged_items.push(tagged);
                    continue;
                };
                let dedup_text = dedup.to_string();
                if seen_keys.contains(&dedup_text) {
                    dedup_removed += 1;
                    continue;
                }
                seen_keys.insert(dedup_text);
                if key == "ids" {
                    merged_items.push(tagged.get("value").cloned().unwrap_or(Value::Null));
                } else {
                    merged_items.push(tagged);
                }
            }
        }
        merged.insert(key.clone(), Value::Array(merged_items));
    }
    if dedup_removed > 0 {
        merged.insert("dedup_removed".to_string(), json!(dedup_removed));
    }

    // Promote stable scalars.
    let mut scalar_candidates: BTreeMap<String, Value> = BTreeMap::new();
    for parser in &successful_parsers {
        let Some(payload) = parser_results.get(parser).and_then(Value::as_object) else {
            continue;
        };
        for (key, value) in payload {
            if FANOUT_DIAGNOSTIC_KEYS.contains(&key.as_str())
                || FANOUT_LIST_RESULT_KEYS.contains(&key.as_str())
                || FANOUT_SINGLE_RESULT_KEYS.contains(&key.as_str())
                || value.is_object()
                || value.is_array()
            {
                continue;
            }
            match scalar_candidates.get(key) {
                Some(previous) if previous != value => {
                    scalar_candidates.insert(key.clone(), json!("__diverged__"));
                }
                _ => {
                    scalar_candidates.insert(key.clone(), value.clone());
                }
            }
        }
    }
    for (key, value) in scalar_candidates {
        if value.as_str() != Some("__diverged__") {
            merged.insert(key, value);
        }
    }
    Value::Object(merged)
}

// ---------------------------------------------------------------------------
// Dispatch entry
// ---------------------------------------------------------------------------

/// Fan-out search tools (`_FANOUT_SEARCH_TOOLS`).
pub const FANOUT_SEARCH_TOOLS: [&str; 13] = [
    "search_functions",
    "search_by_code",
    "get_symbol",
    "get_node_details",
    "query_subgraph",
    "find_paths",
    "find_path_between_module",
    "listup_symbols_matching_file_path",
    "listup_class_matching_path",
    "list_up_entrypoint",
    "trace_flow",
    "trace_flow_between_module",
    "list_possible_calls",
];

/// Tools whose `tools/call` wrapper routes through unified `_dispatch_tool`
/// (`_PROXIED_TOOL_NAMES` non-planner set). Only these get the dispatch-level
/// result post-processing: error-string coercion + `query_engine` +
/// `capability` attachment. Directly-registered unified tools (bridge,
/// workflow, project-context, `inspect_parser_capabilities`, ...) assemble
/// their own payloads — `_run_single_project_context` etc. attach
/// `capability` themselves and never set `query_engine`.
pub const DISPATCH_ROUTED_TOOLS: [&str; 20] = [
    "search_functions",
    "search_by_code",
    "get_symbol",
    "get_node_details",
    "query_subgraph",
    "find_paths",
    "find_path_between_module",
    "listup_symbols_matching_file_path",
    "listup_class_matching_path",
    "list_up_entrypoint",
    "trace_flow",
    "trace_flow_between_module",
    "list_possible_calls",
    "semantic_search",
    "annotate_node",
    "get_ipc_message",
    "list_databases",
    "list_qdrant_collections",
    "find_screen_workflows",
    // explore_graph attaches both keys itself in Python (result["query_engine"]
    // / result["capability"]); the values equal the dispatch defaults, so the
    // setdefault below reproduces the same bytes.
    "explore_graph",
];

/// Tools receiving the unified `_dispatch_tool` default-relationship
/// injection (capability-backed non-android backends only): when the caller
/// omitted relationship types, the per-tool capability defaults are inserted
/// into the payload together with `_capability_default_relationships=True`
/// (→ backend diagnostics report `explicit_request: false`).
fn receives_default_relationship_injection(tool_name: &str) -> bool {
    matches!(
        tool_name,
        "query_subgraph"
            | "find_paths"
            | "trace_flow"
            | "find_path_between_module"
            | "trace_flow_between_module"
            | "find_screen_workflows"
    )
}

fn resolve_backend_for_parser(parser: Option<&str>) -> Backend {
    match capability_for_parser(parser).map(|capability| capability.backend.as_str()) {
        Some("android") => Backend::Android,
        _ => Backend::Cplus,
    }
}

/// Run one backend tool body (the `tool_<name>` of android/cplus backend).
fn run_backend_tool(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    tool_name: &str,
    payload: &Value,
) -> Result<Value, String> {
    match tool_name {
        "search_functions" => tools_search::tool_search_functions(runtime, backend, payload),
        "search_by_code" => tools_search::tool_search_by_code(runtime, backend, payload),
        "get_symbol" => tools_search::tool_get_symbol(runtime, backend, payload),
        "get_node_details" => tools_search::tool_get_node_details(runtime, backend, payload),
        "list_possible_calls" => tools_search::tool_list_possible_calls(runtime, backend, payload),
        "listup_symbols_matching_file_path" => {
            tools_search::tool_listup_symbols_matching_file_path(runtime, backend, payload)
        }
        "listup_class_matching_path" => {
            tools_search::tool_listup_class_matching_path(runtime, backend, payload)
        }
        "list_up_entrypoint" => {
            tools_search::tool_list_up_entrypoint(runtime, backend, payload)
        }
        "trace_flow" => tools_traversal::tool_trace_flow(runtime, backend, payload),
        "trace_flow_between_module" => {
            tools_traversal::tool_trace_flow_between_module(runtime, backend, payload)
        }
        "query_subgraph" => tools_traversal::tool_query_subgraph(runtime, backend, payload),
        "find_paths" => tools_traversal::tool_find_paths(runtime, backend, payload),
        "find_path_between_module" => {
            tools_traversal::tool_find_path_between_module(runtime, backend, payload)
        }
        _ => Err(format!(
            "Tool '{tool_name}' is not available in query engine '{}'.",
            query_engine_for_backend(Some(backend.name()))
        )),
    }
}

/// Unified `_dispatch_tool` graph path — `Some(payload)` khi tool này thuộc
/// graph layer (phase-12 port), `None` để dispatch.rs fallback sang stub.
pub fn dispatch_graph_tool(tool_name: &str, merged: &Value) -> Option<Value> {
    let handled = matches!(
        tool_name,
        "search_functions"
            | "search_by_code"
            | "get_symbol"
            | "get_node_details"
            | "list_possible_calls"
            | "listup_symbols_matching_file_path"
            | "listup_class_matching_path"
            | "list_up_entrypoint"
            | "trace_flow"
            | "trace_flow_between_module"
            | "query_subgraph"
            | "find_paths"
            | "find_path_between_module"
            | "explore_graph"
            | "semantic_search"
            | "annotate_node"
            | "get_ipc_message"
            | "list_databases"
            | "list_qdrant_collections"
            | "inspect_parser_capabilities"
            | "find_callers_of_endpoint"
            | "get_api_call_chain"
            | "analyze_workflow_impact"
            | "find_workflows_containing"
            | "get_project_modules"
            | "get_public_apis"
            | "get_endpoints"
            | "get_module_architecture_summary"
            | "get_project_special_files"
            | "get_framework_context"
            | "find_screen_workflows"
            | "reconstruct_flow"
    );
    if !handled {
        return None;
    }

    // reconstruct_flow là pure function (không cần graph runtime).
    if tool_name == "reconstruct_flow" {
        return Some(flow_reconstruct::tool_reconstruct_flow(merged));
    }

    let selected_parser = crate::dispatch::normalize_parser_type(payload_str(merged, "parser_type"));

    // Fan-out: parser omitted + search tool → dispatch per backend và merge.
    if selected_parser.is_none()
        && FANOUT_SEARCH_TOOLS.contains(&tool_name)
        && !payload_bool(merged, "_fanout")
    {
        let mut merged_mut = merged.as_object().cloned().unwrap_or_default();
        merged_mut.remove("parser_type");
        merged_mut.insert("_fanout".to_string(), json!(true));
        let base_payload = Value::Object(merged_mut);

        let mut backend_names: Vec<Backend> = Vec::new();
        // Python: sorted(parsers) — BACKENDS keys sorted → android, cplus.
        for parser in ["android", "cplus"] {
            let backend = resolve_backend_for_parser(Some(parser));
            if !backend_names.contains(&backend) {
                backend_names.push(backend);
            }
        }

        let mut parser_results: BTreeMap<String, Value> = BTreeMap::new();
        let mut parser_errors: BTreeMap<String, Value> = BTreeMap::new();
        for backend in backend_names {
            let per_engine = {
                let mut object = base_payload.as_object().cloned().unwrap_or_default();
                object.remove("parser_type");
                object.insert("_fanout".to_string(), json!(true));
                Value::Object(object)
            };
            match with_graph_runtime(|runtime| run_backend_tool(runtime, backend, tool_name, &per_engine)) {
                Ok(result) => {
                    parser_results.insert(backend.name().to_string(), result);
                }
                Err(error) => {
                    let error_value = json!({
                        "type": "tool_execution_error",
                        "message": error,
                    });
                    parser_errors.insert(backend.name().to_string(), error_value.clone());
                    parser_results.insert(
                        backend.name().to_string(),
                        json!({"ok": false, "error": error_value}),
                    );
                }
            }
        }
        let mut merged_result = merge_fanout_results(&parser_results, &parser_errors);
        if !parser_errors.is_empty() {
            let parsers_searched = merged_result
                .get("parsers_searched")
                .and_then(Value::as_array)
                .map(Vec::clone)
                .unwrap_or_default();
            if parsers_searched.is_empty() {
                let object = merged_result.as_object_mut().expect("merged object");
                object.insert("ok".to_string(), json!(false));
                let first_engine = parser_errors.keys().next().cloned().unwrap_or_default();
                let first_message = parser_errors
                    .get(&first_engine)
                    .and_then(|error| error.get("message"))
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string();
                object.insert(
                    "error".to_string(),
                    json!({
                        "type": "fanout_failed",
                        "tool": tool_name,
                        "message": format!(
                            "All {} query engine(s) failed to dispatch '{}'. First error from engine '{}': {}",
                            parser_errors.len(),
                            tool_name,
                            first_engine,
                            first_message
                        ),
                        "first_engine": first_engine,
                        "all_errors": Value::Object(parser_errors.clone().into_iter().collect()),
                    }),
                );
                return Some(merged_result);
            }
        }
        if let Some(object) = merged_result.as_object_mut()
            && !object.contains_key("capability")
        {
            object.insert(
                "capability".to_string(),
                crate::dispatch::capability_summary(None),
            );
        }
        return Some(merged_result);
    }

    // Single-parser (hoặc tool không fanout) path.
    let backend = resolve_backend_for_parser(selected_parser.as_deref());
    let mut payload = merged.clone();
    // Python `_dispatch_tool`: relationship defaults injection cho các
    // traversal tool khi caller bỏ qua rel_types (capability-backed,
    // non-android backend). Injection xảy ra TRƯỚC khi backend chạy, nên
    // backend dùng đúng defaults + `_capability_default_relationships` flag.
    if let Some(capability) = capability_for_parser(selected_parser.as_deref())
        && backend != Backend::Android
        && receives_default_relationship_injection(tool_name)
    {
        let provided = |key: &str| {
            payload.get(key).map(|value| match value {
                Value::Null => false,
                Value::Array(items) => !items.is_empty(),
                Value::String(text) => !text.trim().is_empty(),
                _ => true,
            })
            .unwrap_or(false)
        };
        if !provided("relationship_types") && !provided("rel_types") {
            let defaults =
                default_relationships(Some(capability.name.as_str()), Some(tool_name));
            if let Some(object) = payload.as_object_mut() {
                object.insert("relationship_types".to_string(), json!(defaults));
                object.insert("rel_types".to_string(), json!(defaults));
                object.insert(
                    "_capability_default_relationships".to_string(),
                    json!(true),
                );
            }
        }
    }
    let result = match tool_name {
        "explore_graph" => with_graph_runtime(|runtime| {
            tools_explore::tool_explore_graph(runtime, payload.clone())
        }),
        "semantic_search" => with_graph_runtime(|runtime| {
            tools_semantic::tool_semantic_search(runtime, backend, &payload)
        }),
        "annotate_node" => with_graph_runtime(|runtime| {
            tools_search::tool_annotate_node(runtime, backend, &payload)
        }),
        "get_ipc_message" => with_graph_runtime(|runtime| {
            tools_search::tool_get_ipc_message(runtime, backend, &payload)
        }),
        "list_databases" => with_graph_runtime(|runtime| {
            tools_search::tool_list_databases(runtime, payload.clone())
        }),
        "list_qdrant_collections" => Ok(tools_semantic::tool_list_qdrant_collections(&payload)),
        "inspect_parser_capabilities" => with_graph_runtime(|runtime| {
            tools_search::tool_inspect_parser_capabilities(runtime, &payload)
        }),
        "find_callers_of_endpoint" => with_graph_runtime(|runtime| {
            tools_bridge::tool_find_callers_of_endpoint(runtime, payload.clone())
        }),
        "get_api_call_chain" => with_graph_runtime(|runtime| {
            tools_bridge::tool_get_api_call_chain(runtime, payload.clone())
        }),
        "find_workflows_containing" => with_graph_runtime(|runtime| {
            tools_bridge::tool_find_workflows_containing(runtime, payload.clone())
        }),
        "get_project_modules" | "get_public_apis" | "get_endpoints"
        | "get_module_architecture_summary" | "get_project_special_files"
        | "get_framework_context" => with_graph_runtime(|runtime| {
            tools_bridge::dispatch_project_context_tool(runtime, tool_name, payload.clone())
        }),
        "find_screen_workflows" => with_graph_runtime(|runtime| {
            tools_workflows::tool_find_screen_workflows(runtime, payload.clone())
        }),
        "analyze_workflow_impact" => with_graph_runtime(|runtime| {
            tools_workflows::tool_analyze_workflow_impact(runtime, payload.clone())
        }),
        other => with_graph_runtime(|runtime| run_backend_tool(runtime, backend, other, &payload)),
    };
    match result {
        Ok(value) => {
            let mut value = value;
            // Python: chỉ tool được route qua `_dispatch_tool` (proxied) mới
            // nhận post-processing ở dispatch level. Các tool đăng ký trực
            // tiếp (bridge/workflow/project-context/inspect/...) tự lắp payload
            // — `_wrap_dispatch_result` không đụng vào data của chúng.
            let dispatch_routed = DISPATCH_ROUTED_TOOLS.contains(&tool_name);
            if !dispatch_routed {
                return Some(value);
            }
            // `_coerce_error_result`: result {"error": "<str>", "error_type": ...}.
            if let Some(object) = value.as_object()
                && let Some(error_text) = object.get("error").and_then(Value::as_str)
            {
                let error_payload = merged.as_object().cloned().unwrap_or_default();
                let mut legacy = crate::dispatch::build_tool_error(tool_name, &Value::Object(error_payload), error_text);
                if let Some(error_type) = object.get("error_type").and_then(Value::as_str) {
                    if let Some(legacy_error) = legacy.get_mut("error").and_then(Value::as_object_mut) {
                        legacy_error.insert("type".to_string(), json!(error_type));
                    }
                }
                if let Some(diagnostics) = object.get("capability_diagnostics") {
                    if let Some(legacy_error) = legacy.get_mut("error").and_then(Value::as_object_mut) {
                        legacy_error
                            .insert("capability_diagnostics".to_string(), diagnostics.clone());
                    }
                }
                let routing = crate::dispatch::capability_summary(selected_parser.as_deref());
                if let Some(legacy_object) = legacy.as_object_mut() {
                    legacy_object.insert("capability".to_string(), routing);
                }
                return Some(legacy);
            }
            if let Some(object) = value.as_object_mut() {
                object.entry("ok".to_string()).or_insert(json!(true));
                object.remove("backend");
                // Python unified dispatch KHÔNG gắn query_engine cho
                // analyze_workflow_impact (dữ liệu fixture ghi từ live server).
                if tool_name != "analyze_workflow_impact" {
                    object
                        .entry("query_engine".to_string())
                        .or_insert(json!(query_engine_for_backend(Some(backend.name()))));
                }
                object
                    .entry("capability".to_string())
                    .or_insert(crate::dispatch::capability_summary(selected_parser.as_deref()));
            }
            Some(value)
        }
        Err(error) => {
            // `_build_tool_error(tool_name, merged, exc, backend_name)`.
            let mut legacy = crate::dispatch::build_tool_error(tool_name, merged, &error);
            if let Some(object) = legacy.as_object_mut() {
                object.insert(
                    "query_engine".to_string(),
                    json!(query_engine_for_backend(Some(backend.name()))),
                );
            }
            if let Some(error_object) = legacy.get_mut("error").and_then(Value::as_object_mut) {
                error_object.insert(
                    "query_engine".to_string(),
                    json!(query_engine_for_backend(Some(backend.name()))),
                );
            }
            Some(legacy)
        }
    }
}

/// `_relationship_pattern` re-export helper for sibling modules.
pub fn flow_relationships_for(
    capability_name: Option<&str>,
    relationships: &[String],
) -> String {
    let defaults: Vec<String> = match capability_name {
        Some(name) => default_relationships(Some(name), None),
        None => vec!["CALLS".to_string()],
    };
    let filtered: Vec<String> = relationships
        .iter()
        .filter(|item| defaults.contains(item))
        .cloned()
        .collect();
    relationship_pattern(&filtered, "CALLS")
}

/// `project_id_lookup_key` re-export cho sibling modules.
pub fn lookup_key(project_id: Option<&str>) -> Option<String> {
    project_id_lookup_key(project_id)
}

/// Shared prepare helper (project-scope param siblings).
pub fn prepare_params(params: &Map<String, Value>) -> BTreeMap<String, Param> {
    let converted: BTreeMap<String, Value> = params.clone().into_iter().collect();
    prepare_project_scope_parameters(&converted)
        .into_iter()
        .map(|(key, value)| (key, json_to_param(&value)))
        .collect()
}

/// Prepare helper for BTreeMap-backed params (expander/keyword lanes).
pub fn prepare_params_map_btree(params: &BTreeMap<String, Value>) -> BTreeMap<String, Param> {
    prepare_project_scope_parameters(params)
        .into_iter()
        .map(|(key, value)| (key, json_to_param(&value)))
        .collect()
}


/// JSON null→"" helper (chuỗi rỗng cho payload null).
pub fn null_str(value: Value) -> Value {
    if value.is_null() { json!("") } else { value }
}

/// JSON null→default-string helper.
pub fn null_str_or(value: Value, default: &str) -> Value {
    if value.is_null() { json!(default) } else { value }
}

/// JSON null→fallback helper.
pub fn null_or(value: Value, fallback: Value) -> Value {
    if value.is_null() { fallback } else { value }
}

/// Chuỗi hoá JSON value (None an toàn).
pub fn str_of(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// Build a JSON map from (key, value) pairs.
pub fn map_from_static<'a>(items: impl IntoIterator<Item = (&'a str, Value)>) -> Map<String, Value> {
    items
        .into_iter()
        .map(|(key, value)| (key.to_string(), value))
        .collect()
}

//! Vector lane for the unified graph server — phases 03/04 of
//! `plans/260915-2027-vector-lane-rust-port`.
//!
//! Query embedding runs natively (`cortex-embed` ONNX, jina-v3, `Plane::Code`)
//! and qdrant search goes through one of two backends:
//!
//! * `Remote` — REST (`/points/search`) for projects registered with
//!   `storage_backend: "remote"` (wire protocol, mirrors `mind::qdrant`);
//! * `Local` — the [`crate::vector_sidecar`] python worker reading the same
//!   store the python ingest writes (QdrantLocal pickles must not be parsed
//!   in Rust; see the plan ADR D2).
//!
//! Merge/dedupe/provenance semantics mirror `qdrant_query_support.merge_hits`
//! byte-for-byte: dedupe by `str(id)` keeping the higher score, stable
//! sort by score desc, cut to `top_k`.

use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};

use serde_json::{json, Map, Value};

use cortex_embed::{Backend, Embedder, OnnxEmbedder, Plane, spec_from_env};

use crate::project_registry;
use crate::vector_sidecar;

// ---------------------------------------------------------------------------
// Store resolution
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub enum VectorStore {
    Local { store_root: PathBuf },
    Remote { url: String, api_key: Option<String> },
}

/// `get_code_qdrant_store` contract: registered projects with
/// `storage_backend: "remote"` + `qdrant_url` route to the wire protocol;
/// everything else (including unregistered ids — the code lane never raises
/// for those, unlike mind) reads the local instance store.
pub fn resolve_store(project_id: Option<&str>) -> Result<VectorStore, String> {
    if let Some(pid) = project_id.map(str::trim).filter(|value| !value.is_empty()) {
        if let Ok(targets) = project_registry::resolve_project_targets(Some(pid), None) {
            if targets.storage_backend == "remote"
                && let Some((url, api_key)) = remote_qdrant_url(targets.remote_config.as_ref())
            {
                return Ok(VectorStore::Remote { url, api_key });
            }
        }
    }
    Ok(VectorStore::Local {
        store_root: local_store_root(),
    })
}

fn remote_qdrant_url(remote_config: Option<&Value>) -> Option<(String, Option<String>)> {
    let remote = remote_config?;
    let url = remote
        .get("qdrant_url")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())?
        .trim_end_matches('/')
        .to_string();
    let api_key = remote
        .get("qdrant_api_key")
        .and_then(Value::as_str)
        .map(str::to_string);
    Some((url, api_key))
}

/// Store root for the code plane — `QDRANT_CODE_PATH` or the instance layout.
pub fn local_store_root() -> PathBuf {
    if let Ok(path) = std::env::var("QDRANT_CODE_PATH")
        && !path.trim().is_empty()
    {
        return PathBuf::from(path);
    }
    let home = match std::env::var("CORTEX_DATA_HOME") {
        Ok(home) if !home.trim().is_empty() => PathBuf::from(home.trim().to_string()),
        _ => match std::env::var("HOME") {
            Ok(home) if !home.is_empty() => PathBuf::from(home).join(".cortext-harness"),
            _ => PathBuf::from(".cortext-harness"),
        },
    };
    let instance = std::env::var("CORTEX_STORAGE_INSTANCE")
        .map(|value| value.trim().to_string())
        .unwrap_or_else(|_| "default".to_string());
    let instance = if instance.is_empty() { "default".to_string() } else { instance };
    home.join("v1").join("instances").join(instance).join("qdrant").join("code")
}

// ---------------------------------------------------------------------------
// Query embedding (cortex-embed, Plane::Code)
// ---------------------------------------------------------------------------

pub fn embed_query(query: &str) -> Result<Vec<f64>, String> {
    match Backend::from_env() {
        Backend::Onnx => {
            static EMBEDDER: OnceLock<Mutex<Option<OnnxEmbedder>>> = OnceLock::new();
            if let Some(note) = Backend::device_note(Plane::Code) {
                eprintln!("[vector-lane] {note}");
            }
            let holder = EMBEDDER.get_or_init(|| Mutex::new(None));
            let mut guard =
                holder.lock().map_err(|_| "embedder lock poisoned".to_string())?;
            if guard.is_none() {
                let spec = spec_from_env(Plane::Code).map_err(|error| error.to_string())?;
                let embedder = OnnxEmbedder::new(spec).map_err(|error| error.to_string())?;
                eprintln!("[vector-lane] onnx backend loaded model={}", embedder.spec().id);
                *guard = Some(embedder);
            }
            let embedder = guard.as_ref().ok_or("onnx embedder unavailable")?;
            let vectors = embedder
                .embed(&[query.to_string()])
                .map_err(|error| error.to_string())?;
            Ok(vectors
                .into_iter()
                .next()
                .ok_or("empty embedding response")?
                .into_iter()
                .map(f64::from)
                .collect())
        }
        Backend::Python => Err(
            "the code-plane python embedder fallback is not provided; \
             use CORTEX_EMBED_BACKEND=onnx (requires `make embed-artifacts`) \
             or run the whole MCP server with CORTEX_MCP_BACKEND=python"
                .to_string(),
        ),
    }
}

// ---------------------------------------------------------------------------
// Store operations
// ---------------------------------------------------------------------------

/// `list_collections` — names in store order (local: client order; remote:
/// server response order).
pub fn list_collection_names(store: &VectorStore) -> Result<Vec<String>, String> {
    match store {
        VectorStore::Local { store_root } => {
            vector_sidecar::list_collections(store_root)
        }
        VectorStore::Remote { url, api_key } => {
            let payload = remote_request(url, api_key.as_deref(), "GET", "/collections", None)?;
            let collections = payload
                .pointer("/result/collections")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            Ok(collections
                .iter()
                .filter_map(|item| item.get("name").and_then(Value::as_str))
                .map(str::to_string)
                .collect())
        }
    }
}

/// `vector_sizes` — `{"default": 1024}` or a named-vector size map.
pub fn collection_vector_sizes(
    store: &VectorStore,
    collection: &str,
) -> Result<Map<String, Value>, String> {
    match store {
        VectorStore::Local { store_root } => {
            vector_sidecar::collection_vector_sizes(store_root, collection)
        }
        VectorStore::Remote { url, api_key } => {
            let payload =
                remote_request(url, api_key.as_deref(), "GET", &format!("/collections/{collection}"), None)?;
            let mut sizes = Map::new();
            let vectors = payload.pointer("/result/config/params/vectors");
            if let Some(Value::Object(map)) = vectors {
                if map.contains_key("size") {
                    if let Some(size) = map.get("size").and_then(Value::as_i64) {
                        sizes.insert("default".to_string(), json!(size));
                    }
                } else {
                    for (name, params) in map {
                        if let Some(size) = params.get("size").and_then(Value::as_i64) {
                            sizes.insert(name.clone(), json!(size));
                        }
                    }
                }
            }
            Ok(sizes)
        }
    }
}

/// One qdrant search — raw hit objects `{id, version, score, payload}`.
/// Payload arrives narrowed (`text` excluded) exactly like python's
/// `PayloadSelectorExclude(["text"])`.
pub fn search_collection(
    store: &VectorStore,
    collection: &str,
    vector: &[f64],
    vector_name: Option<&str>,
    limit: usize,
    filter: Option<&Value>,
) -> Result<Vec<Value>, String> {
    match store {
        VectorStore::Local { store_root } => {
            vector_sidecar::search_with_using(store_root, collection, vector, vector_name, limit, filter)
        }
        VectorStore::Remote { url, api_key } => {
            let mut body = json!({
                "vector": vector,
                "limit": limit,
                "with_payload": {"exclude": ["text"]},
                "with_vectors": false,
            });
            if let Some(filter) = filter {
                body["filter"] = filter.clone();
            }
            if let Some(using) = vector_name {
                body["using"] = json!(using);
            }
            let payload = remote_request(
                url,
                api_key.as_deref(),
                "POST",
                &format!("/collections/{collection}/points/search"),
                Some(&body),
            )?;
            Ok(payload
                .get("result")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default())
        }
    }
}

fn remote_request(
    url: &str,
    api_key: Option<&str>,
    method: &str,
    path: &str,
    body: Option<&Value>,
) -> Result<Value, String> {
    let mut request = ureq::request(method, &format!("{url}{path}")).timeout(std::time::Duration::from_secs(30));
    if let Some(key) = api_key {
        request = request.set("api-key", key);
    }
    let response = match body {
        Some(body) => request
            .set("Content-Type", "application/json")
            .send_string(&body.to_string()),
        None => request.call(),
    }
    .map_err(|error| format!("qdrant remote error: {error}"))?;
    response
        .into_string()
        .map_err(|error| format!("qdrant remote read error: {error}"))
        .and_then(|text| serde_json::from_str(&text).map_err(|error| error.to_string()))
}

// ---------------------------------------------------------------------------
// Filter + merge semantics (python contract mirrors)
// ---------------------------------------------------------------------------

/// `qdrant_project_filter` — `match.any` prefix-expansion keys, or `None`
/// (no filter) for an unscoped query.
pub fn project_scope_filter(project_id: Option<&str>) -> Option<Value> {
    let query_key = project_id
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(|value| value.to_lowercase())?;
    let mut keys = vec![query_key.clone()];
    if let Ok(known) = project_registry::list_registered_projects(None) {
        for known_id in known {
            let known_key = known_id.trim().to_lowercase();
            if known_key.starts_with(&query_key) && !keys.contains(&known_key) {
                keys.push(known_key);
            }
        }
    }
    keys.sort();
    Some(json!({
        "must": [
            {"key": "project_id_normalized", "match": {"any": keys}},
        ],
    }))
}

/// `merge_hits` — dedupe by `str(id)` keeping the higher score, stable sort
/// by score desc, cut to `top_k`.
pub fn merge_hits(per_collection_hits: Vec<Vec<Value>>, top_k: usize) -> Vec<Value> {
    // Insertion order of first sighting, value replaced on higher score —
    // python's dict semantics.
    let mut order: Vec<String> = Vec::new();
    let mut combined: std::collections::HashMap<String, Value> = std::collections::HashMap::new();
    for hits in per_collection_hits {
        for item in hits {
            let point_id = item
                .get("id")
                .map(value_to_id_string)
                .unwrap_or_default();
            let score = item.get("score").and_then(Value::as_f64).unwrap_or(0.0);
            match combined.get(&point_id) {
                None => {
                    order.push(point_id.clone());
                    combined.insert(point_id, item);
                }
                Some(existing) => {
                    let existing_score = existing.get("score").and_then(Value::as_f64).unwrap_or(0.0);
                    if score > existing_score {
                        combined.insert(point_id, item);
                    }
                }
            }
        }
    }
    let mut merged: Vec<Value> = order
        .into_iter()
        .filter_map(|id| combined.remove(&id))
        .collect();
    merged.sort_by(|a, b| {
        let a_score = a.get("score").and_then(Value::as_f64).unwrap_or(0.0);
        let b_score = b.get("score").and_then(Value::as_f64).unwrap_or(0.0);
        b_score.partial_cmp(&a_score).unwrap_or(std::cmp::Ordering::Equal)
    });
    merged.truncate(top_k);
    merged
}

fn value_to_id_string(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

/// `filter_collections_for_vector` — keep collections whose default/named
/// vector size matches the embedding length. Returns the selected
/// `(collection, named-vector)` pairs plus python-shaped error rows.
pub fn filter_collections_for_vector(
    store: &VectorStore,
    collections: &[String],
    vector_len: usize,
) -> (Vec<(String, Option<String>)>, Vec<Value>) {
    let mut selected: Vec<(String, Option<String>)> = Vec::new();
    let mut errors: Vec<Value> = Vec::new();
    for collection in collections {
        let sizes = match collection_vector_sizes(store, collection) {
            Ok(sizes) => sizes,
            Err(error) => {
                errors.push(json!({"collection": collection, "error": error}));
                continue;
            }
        };
        if sizes.is_empty() {
            errors.push(json!({"collection": collection, "error": "No matching vector size."}));
            continue;
        }
        let default_only = sizes.len() == 1 && sizes.contains_key("default");
        if default_only {
            let size = sizes.get("default").and_then(Value::as_i64).unwrap_or(0);
            if size == vector_len as i64 {
                selected.push((collection.clone(), None));
            } else {
                errors.push(json!({
                    "collection": collection,
                    "error": format!("Vector size mismatch (expected {vector_len}, got {size})"),
                }));
            }
            continue;
        }
        let named = sizes
            .iter()
            .find(|(_, size)| size.as_i64() == Some(vector_len as i64))
            .map(|(name, _)| name.clone());
        match named {
            Some(name) => selected.push((collection.clone(), Some(name))),
            None => errors.push(json!({
                "collection": collection,
                "error": format!("No matching vector size for {vector_len}."),
            })),
        }
    }
    (selected, errors)
}

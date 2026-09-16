//! Vector lane for the unified graph server — phases 03/04 of
//! `plans/260915-2027-vector-lane-rust-port`, flipped local-native by
//! `plans/260916-1154-native-vector-ingest-local` (phase-03 reader wire).
//!
//! Query embedding runs natively (`cortex-embed` ONNX, jina-v3, `Plane::Code`)
//! and qdrant search goes through one of two backends:
//!
//! * `Remote` — REST (`/points/search`) for projects registered with
//!   `storage_backend: "remote"` (wire protocol, mirrors `mind::qdrant`);
//! * `Local` — dual-mode under `CORTEX_VECTOR_BACKEND` (D5): when the local
//!   lane is native (`=rust`, or unset after the phase-05 flip) the lock-free
//!   [`cortex_storage::qdrant::LocalQdrantReader`] reads the Rust JSON store
//!   the native writer owns (no lease — writer durability is atomic-rename,
//!   snapshots revalidate on mtime+size); otherwise the code lane falls back
//!   to the [`crate::vector_sidecar`] python worker over the legacy
//!   qdrant-client pickle store (mind/doc lane keeps the sidecar either way).
//!
//! Merge/dedupe/provenance semantics mirror `qdrant_query_support.merge_hits`
//! byte-for-byte: dedupe by `str(id)` keeping the higher score, stable
//! sort by score desc, cut to `top_k`.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use serde_json::{json, Map, Value};

use cortex_embed::{Backend, Embedder, OnnxEmbedder, Plane, spec_from_env};
use cortex_storage::qdrant::{local_native_enabled, LocalQdrantReader};

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

/// Store root for the code plane. Resolution goes through the SHARED
/// `cortex_storage::resolve_storage` (review fix: the writer and the reader
/// must derive the same root — env-first `QDRANT_CODE_PATH`, then config
/// file, then the instance-derived default with `~` expansion + instance
/// normalization). If resolution fails outright (e.g. a broken config),
/// fall back to the minimal env chain so the reader keeps degrading loudly
/// at store-open time instead of erroring before it can name a path.
pub fn local_store_root() -> PathBuf {
    if let Ok(resolved) = cortex_storage::config::resolve_storage(
        std::path::Path::new("."),
        None,
        &cortex_storage::config::ResolveOverrides::default(),
    ) && let Ok(path) = resolved.path_for_role(cortex_storage::StorageRole::Code.as_str())
    {
        return path.to_path_buf();
    }
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

/// One lock-free native reader per store root, process-wide. The reader's
/// mtime+size-revalidated snapshot cache must survive across the ops of one
/// tool call (list → sizes → search) — reopening per op would re-parse the
/// whole store every time.
fn local_native_reader(store_root: &Path) -> Result<Arc<LocalQdrantReader>, String> {
    static CACHE: OnceLock<Mutex<HashMap<PathBuf, Arc<LocalQdrantReader>>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let mut guard = cache.lock().map_err(|_| "local reader cache poisoned".to_string())?;
    if let Some(reader) = guard.get(store_root) {
        return Ok(reader.clone());
    }
    let reader = LocalQdrantReader::open(store_root).map_err(|error| error.to_string())?;
    guard.insert(store_root.to_path_buf(), Arc::new(reader));
    Ok(guard.get(store_root).expect("just inserted").clone())
}

/// `list_collections` — names in store order (local: client order; remote:
/// server response order).
pub fn list_collection_names(store: &VectorStore) -> Result<Vec<String>, String> {
    list_collection_names_with(store, local_native_enabled())
}

fn list_collection_names_with(store: &VectorStore, native_local: bool) -> Result<Vec<String>, String> {
    match store {
        VectorStore::Local { store_root } => {
            if native_local {
                local_native_reader(store_root)?.list_collection_names().map_err(|e| e.to_string())
            } else {
                vector_sidecar::list_collections(store_root)
            }
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

/// REST `config.params.vectors` → `{"default": 1024}` or a named size map —
/// shared by the remote and native-local arms (phase-01 gave the local
/// `get_collection_info` exactly this shape).
fn parse_collection_vector_sizes(payload: &Value) -> Map<String, Value> {
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
    sizes
}

/// `vector_sizes` — `{"default": 1024}` or a named-vector size map.
pub fn collection_vector_sizes(
    store: &VectorStore,
    collection: &str,
) -> Result<Map<String, Value>, String> {
    collection_vector_sizes_with(store, collection, local_native_enabled())
}

fn collection_vector_sizes_with(
    store: &VectorStore,
    collection: &str,
    native_local: bool,
) -> Result<Map<String, Value>, String> {
    match store {
        VectorStore::Local { store_root } => {
            if native_local {
                let info = local_native_reader(store_root)?
                    .get_collection_info(collection)
                    .map_err(|e| e.to_string())?;
                Ok(parse_collection_vector_sizes(&info))
            } else {
                vector_sidecar::collection_vector_sizes(store_root, collection)
            }
        }
        VectorStore::Remote { url, api_key } => {
            let payload =
                remote_request(url, api_key.as_deref(), "GET", &format!("/collections/{collection}"), None)?;
            Ok(parse_collection_vector_sizes(&payload))
        }
    }
}

/// Native-hit lane shape: python's `PayloadSelectorExclude(["text"])` parity
/// (post-strip — the engine returns full payloads) minus the inert null
/// `vector` key. Native hits carry no `version` field (QdrantLocal hits do);
/// that divergence is pinned by the shape-audit in phase01-engine.md and
/// tolerated by the parity comparator.
fn lane_hit_shape(mut hit: Value) -> Value {
    if let Some(payload) = hit.get_mut("payload").and_then(Value::as_object_mut) {
        payload.remove("text");
    }
    if let Some(object) = hit.as_object_mut() {
        if object.get("vector").map(Value::is_null).unwrap_or(false) {
            object.remove("vector");
        }
    }
    hit
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
    search_collection_with(store, collection, vector, vector_name, limit, filter, local_native_enabled())
}

#[allow(clippy::too_many_arguments)]
fn search_collection_with(
    store: &VectorStore,
    collection: &str,
    vector: &[f64],
    vector_name: Option<&str>,
    limit: usize,
    filter: Option<&Value>,
    native_local: bool,
) -> Result<Vec<Value>, String> {
    match store {
        VectorStore::Local { store_root } => {
            if native_local {
                let hits = local_native_reader(store_root)?
                    .search(collection, vector, limit, filter, true, false, vector_name)
                    .map_err(|e| e.to_string())?;
                Ok(hits.into_iter().map(lane_hit_shape).collect())
            } else {
                vector_sidecar::search_with_using(store_root, collection, vector, vector_name, limit, filter)
            }
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

#[cfg(test)]
mod tests {
    use super::*;

    /// Native local arms (hatch `=rust`, post-flip unset): list/sizes/search
    /// over the JSON store with lane-hit shape — `text` stripped (python
    /// PayloadSelectorExclude parity), null `vector` dropped, no `version`.
    #[test]
    fn local_native_arms_read_json_store() {
        let isolation = tempfile::tempdir().unwrap();
        let store_root = isolation.path().to_path_buf();

        // Seed the JSON store through the owning writer engine.
        let resolved = cortex_storage::config::resolve_storage(
            isolation.path(),
            None,
            &cortex_storage::config::ResolveOverrides {
                qdrant_code_path: Some(store_root.to_string_lossy().into_owned()),
                ..Default::default()
            },
        )
        .unwrap();
        resolved.ensure_directories().unwrap();
        let writer = cortex_storage::qdrant::LocalQdrantStore::open(
            &resolved,
            cortex_storage::StorageRole::Code.as_str(),
        )
        .unwrap();
        writer
            .create_collection("code", &json!({"size": 4, "distance": "Cosine"}))
            .unwrap();
        writer
            .upsert(
                "code",
                &[
                    json!({
                        "id": "hit-1",
                        "vector": [1.0, 0.0, 0.0, 0.0],
                        "payload": {
                            "project_id_normalized": "proj",
                            "text": "raw body that must never cross merge_hits",
                            "symbol": "main",
                        },
                    }),
                    json!({
                        "id": "hit-2",
                        "vector": [0.0, 1.0, 0.0, 0.0],
                        "payload": {
                            "project_id_normalized": "other",
                            "text": "foreign project",
                        },
                    }),
                ],
            )
            .unwrap();

        let store = VectorStore::Local { store_root: store_root.clone() };

        assert_eq!(list_collection_names_with(&store, true).unwrap(), vec!["code"]);
        let sizes = collection_vector_sizes_with(&store, "code", true).unwrap();
        assert_eq!(sizes.get("default"), Some(&json!(4)));
        assert!(sizes.get("text").is_none(), "anonymous config must not leak param keys as sizes");

        let filter = json!({
            "must": [{"key": "project_id_normalized", "match": {"any": ["proj"]}}],
        });
        let hits = search_collection_with(
            &store, "code", &[1.0, 0.0, 0.0, 0.0], None, 5, Some(&filter), true,
        )
        .unwrap();
        assert_eq!(hits.len(), 1, "scope filter keeps only the matching project");
        let hit = &hits[0];
        assert_eq!(hit["id"], "hit-1");
        assert!(hit.get("score").and_then(Value::as_f64).is_some());
        assert!(hit.get("version").is_none(), "native hits carry no version (phase-01 audit)");
        assert!(hit.get("vector").is_none(), "inert null vector key is dropped");
        let payload = hit.get("payload").and_then(Value::as_object).unwrap();
        assert!(!payload.contains_key("text"), "text must be stripped lane-side");
        assert_eq!(payload.get("symbol"), Some(&json!("main")));

        // Merged across collections exactly like the python merge contract.
        let merged = merge_hits(vec![hits.clone(), hits.clone()], 2);
        assert_eq!(merged.len(), 1, "dedupe by str(id) keeps one entry");

        writer.close();
    }

    /// Pre-flip default: unset flag keeps the code lane on the sidecar path
    /// (behavior identical to today). Hermetic routing proof: on a
    /// quarantined root (JSON store present, pickle subtree gone) the
    /// sidecar arm hits the loud refusal guard before any spawn, while the
    /// native arm reads the JSON store fine.
    #[test]
    fn local_default_still_routes_to_sidecar_before_flip() {
        let isolation = tempfile::tempdir().unwrap();
        let store_root = isolation.path().to_path_buf();
        std::fs::write(
            store_root.join("cortex-local-store.json"),
            "{\"collections\":{}}",
        )
        .unwrap();
        let store = VectorStore::Local { store_root };

        let error = list_collection_names_with(&store, false).unwrap_err();
        assert!(
            error.contains("refuses") && error.contains("JSON vector store"),
            "pre-flip local lane stays on the (guarded) sidecar path, got: {error}"
        );
        assert_eq!(
            list_collection_names_with(&store, true).unwrap(),
            Vec::<String>::new(),
            "native arm reads the quarantined root's JSON store without a lease"
        );
    }
}

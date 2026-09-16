//! Phase-06 native embedding driver — store resolution + the
//! `sync_vector_documents` contract (primary_vector_sync.py:262-374).
//!
//! Store resolution mirrors `tools.common.local_qdrant.get_code_qdrant_store`
//! for the subset that can be served natively:
//!
//! 1. `CORTEX_STORAGE_PROJECT_ID` set → project registry decides:
//!    `storage_backend == "remote"` + `remote.qdrant_url` → HTTP Qdrant
//!    server (the lane Python children write on, byte-for-byte via REST).
//! 2. Everything else → the LOCAL lane. Native-vector-ingest-local (rev2):
//!    the Rust JSON engine (`LocalQdrantStore`) is the local-lane owner, but
//!    only under the `CORTEX_VECTOR_BACKEND` hatch (D5): `=rust` opts in;
//!    unset (pre-flip) or `=python` keeps the native local pass OFF — code
//!    vectors stay FROZEN at zero writes (python analyzer children do not
//!    embed since phase-08; there is no "delegate to python children" path).
//!    Phase-05 of the plan flips the default (`unset` → native).

use std::collections::BTreeMap;

use cortex_embed::{Embedder, Plane};
use cortex_storage::config::{resolve_storage, ResolveOverrides};
use cortex_storage::qdrant::LocalQdrantStore;
use cortex_storage::qdrant_remote::RemoteQdrantStore;
use serde_json::{Map, Value, json};

use crate::registry;
use crate::vector_sync::{
    self, QDRANT_UPSERT_BATCH, VectorDocument, validate_collection_name,
};

/// Outcome of the fail-closed store resolution.
#[derive(Debug)]
pub enum NativeStore {
    Remote(RemoteQdrantStore),
    /// Native local JSON engine (`cortex-local-store.json`) — gated behind
    /// `CORTEX_VECTOR_BACKEND=rust` until the phase-05 flip.
    Local(LocalQdrantStore),
    /// Local lane stays OFF — code vectors are FROZEN at zero writes.
    Unsupported(String),
}

/// Store-agnostic write surface behind `sync_vector_documents` — the trait
/// seam with exactly two impls (remote REST wire / local JSON engine).
pub trait VectorWriteOps {
    fn collection_exists(&self, collection: &str) -> Result<bool, String>;
    /// REST-shaped `get_collection_info` (drift-guard source).
    fn collection_info(&self, collection: &str) -> Result<Value, String>;
    fn create_collection_body(&self, collection: &str, body: &Value) -> Result<(), String>;
    /// Ingest the pass's points; durability is defined by [`Self::finalize`].
    fn upsert_batches(&self, collection: &str, points: &[Value]) -> Result<(), String>;
    fn delete_by_filter(&self, collection: &str, filter: &Value) -> Result<(), String>;
    fn create_payload_index(&self, collection: &str, field: &str) -> Result<(), String>;
    /// Close the pass durably (remote: every call already waited; local: the
    /// single closing flush of the persist-mode bulk pass).
    fn finalize(&self) -> Result<(), String> {
        Ok(())
    }
}

impl VectorWriteOps for RemoteQdrantStore {
    fn collection_exists(&self, collection: &str) -> Result<bool, String> {
        RemoteQdrantStore::collection_exists(self, collection).map_err(|error| error.to_string())
    }

    fn collection_info(&self, collection: &str) -> Result<Value, String> {
        RemoteQdrantStore::get_collection_info(self, collection).map_err(|error| error.to_string())
    }

    fn create_collection_body(&self, collection: &str, body: &Value) -> Result<(), String> {
        RemoteQdrantStore::create_collection_body(self, collection, body)
            .map_err(|error| error.to_string())
    }

    fn upsert_batches(&self, collection: &str, points: &[Value]) -> Result<(), String> {
        let size = QDRANT_UPSERT_BATCH.max(1);
        for start in (0..points.len()).step_by(size) {
            let batch = &points[start..(start + size).min(points.len())];
            // Only the final batch waits: the flush at the function boundary
            // is the durability contract (primary_vector_sync.py:343-352).
            let is_last = start + size >= points.len();
            self.upsert_wait(collection, batch, is_last)
                .map_err(|error| error.to_string())?;
        }
        Ok(())
    }

    fn delete_by_filter(&self, collection: &str, filter: &Value) -> Result<(), String> {
        self.delete(collection, None, Some(filter))
            .map(|_| ())
            .map_err(|error| error.to_string())
    }

    fn create_payload_index(&self, collection: &str, field: &str) -> Result<(), String> {
        RemoteQdrantStore::create_payload_index(self, collection, field, None)
            .map_err(|error| error.to_string())
    }
}

impl VectorWriteOps for LocalQdrantStore {
    fn collection_exists(&self, collection: &str) -> Result<bool, String> {
        Ok(LocalQdrantStore::collection_exists(self, collection))
    }

    fn collection_info(&self, collection: &str) -> Result<Value, String> {
        LocalQdrantStore::get_collection_info(self, collection).map_err(|error| error.to_string())
    }

    fn create_collection_body(&self, collection: &str, body: &Value) -> Result<(), String> {
        // hnsw_config/quantization_config keys are inert on the local engine
        // (matches the Python one-time inert-tuning warning).
        LocalQdrantStore::create_collection(self, collection, body).map_err(|error| error.to_string())
    }

    fn upsert_batches(&self, collection: &str, points: &[Value]) -> Result<(), String> {
        // Persist-mode bulk (phase-01): one in-memory ingest, no per-batch
        // full-store serialization; finalize() flushes exactly once.
        LocalQdrantStore::upsert_deferred(self, collection, points).map_err(|error| error.to_string())
    }

    fn delete_by_filter(&self, collection: &str, filter: &Value) -> Result<(), String> {
        LocalQdrantStore::delete_deferred(self, collection, None, Some(filter))
            .map_err(|error| error.to_string())
    }

    fn create_payload_index(&self, collection: &str, field: &str) -> Result<(), String> {
        // Idempotent on the local engine (phase-01) — safe to re-issue.
        LocalQdrantStore::create_payload_index(self, collection, field, None)
            .map_err(|error| error.to_string())
    }

    fn finalize(&self) -> Result<(), String> {
        LocalQdrantStore::flush(self).map_err(|error| error.to_string())
    }
}

fn env_nonempty(key: &str) -> Option<String> {
    std::env::var(key).ok().map(|v| v.trim().to_string()).filter(|v| !v.is_empty())
}

/// `python_id_lookup_key` semantics (strip + casefold) live in
/// cortex-graph-writer::project_scope; the registry match is on the same key.
fn lookup_key(value: &str) -> String {
    cortex_graph_writer::project_scope::project_id_lookup_key(Some(value)).unwrap_or_default()
}

/// Read `storage_backend` + `remote.qdrant_url`/`remote.qdrant_api_key` for
/// one project from the harness config dir — the same files
/// `tools.common.project_registry` merges (every `*.json`, sorted).
fn registry_backend(project_id: &str) -> Option<(String, Option<String>, Option<String>)> {
    let config_dir = match env_nonempty("CORTEX_HARNESS_CONFIG_PATH") {
        Some(path) => {
            let resolved = std::path::Path::new(&path);
            resolved.parent()?.to_path_buf()
        }
        None => registry::repo_root().join(".cortext-harness/config"),
    };
    let mut files: Vec<std::path::PathBuf> = std::fs::read_dir(&config_dir)
        .ok()?
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.extension().map(|ext| ext == "json").unwrap_or(false) && path.is_file()
        })
        .collect();
    files.sort();
    let wanted = lookup_key(project_id);
    for path in files {
        let Ok(text) = std::fs::read_to_string(&path) else { continue };
        let Ok(payload) = serde_json::from_str::<Value>(&text) else { continue };
        let project = payload.get("project")?.as_object()?;
        let entry_id = ["code", "name"]
            .iter()
            .filter_map(|key| project.get(*key))
            .filter_map(Value::as_str)
            .map(str::trim)
            .find(|value| !value.is_empty());
        let Some(entry_id) = entry_id else { continue };
        if lookup_key(entry_id) != wanted {
            continue;
        }
        let backend = payload
            .get("storage_backend")
            .and_then(Value::as_str)
            .unwrap_or("local")
            .to_string();
        let remote = payload.get("remote").and_then(Value::as_object);
        let url = remote
            .and_then(|r| r.get("qdrant_url"))
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string);
        let api_key = remote
            .and_then(|r| r.get("qdrant_api_key"))
            .and_then(Value::as_str)
            .map(str::to_string);
        return Some((backend, url, api_key));
    }
    None
}

/// Local-lane OFF reason — the frozen semantics (D5, red-team C1): there is
/// no "delegate to python children" path (python analyzer children do not
/// embed since phase-08), so OFF means zero writes, period.
fn frozen_reason(context: &str) -> NativeStore {
    NativeStore::Unsupported(format!(
        "{context}: native local lane is OFF (CORTEX_VECTOR_BACKEND is not 'rust') — code \
         vectors stay FROZEN at zero writes (python analyzer children do not embed since \
         phase-08); set CORTEX_VECTOR_BACKEND=rust to opt in"
    ))
}

/// Resolve the local JSON engine via the shared cortex-storage resolver
/// (env-first: `QDRANT_CODE_PATH`, then the instance-derived default).
fn open_local_native_store(context: &str) -> NativeStore {
    match resolve_storage(std::path::Path::new("."), None, &ResolveOverrides::default()) {
        Ok(resolved) => {
            match LocalQdrantStore::open(&resolved, cortex_storage::StorageRole::Code.as_str()) {
                Ok(store) => NativeStore::Local(store),
                Err(error) => NativeStore::Unsupported(format!(
                    "native local vector store failed to open ({context}): {error}"
                )),
            }
        }
        Err(error) => NativeStore::Unsupported(format!(
            "native local vector store resolution failed ({context}): {error}"
        )),
    }
}

/// Mirror of `get_code_qdrant_store` for the natively-servable subset.
pub fn open_native_store(qdrant_url: Option<&str>) -> NativeStore {
    let local_native = cortex_storage::qdrant::local_native_enabled();
    if let Some(project_id) = env_nonempty("CORTEX_STORAGE_PROJECT_ID") {
        match registry_backend(&project_id) {
            Some((backend, url, api_key)) => {
                if backend == "remote" {
                    if let Some(url) = url {
                        return match RemoteQdrantStore::new(&url, api_key.as_deref(), None, Some("code"))
                        {
                            Ok(store) => NativeStore::Remote(store),
                            Err(error) => NativeStore::Unsupported(format!(
                                "remote qdrant store for project {project_id:?} failed to open: {error}"
                            )),
                        };
                    }
                    // factory fallback: remote backend without qdrant_url → local store
                    if local_native {
                        return open_local_native_store(&format!(
                            "project {project_id:?} is backend=remote without remote.qdrant_url \
                             (factory falls back to the embedded store)"
                        ));
                    }
                    return frozen_reason(&format!(
                        "project {project_id:?} is backend=remote without remote.qdrant_url \
                         (factory falls back to the embedded store)"
                    ));
                }
                if local_native {
                    return open_local_native_store(&format!(
                        "project {project_id:?} uses storage_backend={backend:?} (embedded engine)"
                    ));
                }
                return frozen_reason(&format!(
                    "project {project_id:?} uses storage_backend={backend:?} (embedded engine)"
                ));
            }
            None => {
                // Refusing to guess survives the hatch: an unregistered
                // project has no backend contract at all.
                return NativeStore::Unsupported(format!(
                    "project {project_id:?} is not registered in the harness config: refusing \
                     to guess a storage backend; vector lane stays frozen (zero writes)"
                ));
            }
        }
    }
    let locator = qdrant_url.map(str::trim).filter(|value| !value.is_empty());
    match locator {
        // Unregistered locator-shaped URLs are what `_local_path` rejects as
        // `RemoteQdrantUnsupportedError` in Python — mirror the fail-closed
        // behaviour without pretending we can write the embedded engine.
        Some(url) if url.starts_with("http://") || url.starts_with("https://") => {
            NativeStore::Unsupported(format!(
                "http-shaped QDRANT_CODE_PATH without CORTEX_STORAGE_PROJECT_ID is rejected by \
                 the Python reference too (RemoteQdrantUnsupportedError): {url}"
            ))
        }
        _ if local_native => open_local_native_store("path-shaped QDRANT_CODE_PATH locator"),
        _ => frozen_reason("local embedded qdrant store (qdrant_client(path=...))"),
    }
}

/// `vector_sizes` from a get_collection_info REST response.
pub fn vector_sizes(info: &Value) -> BTreeMap<String, i64> {
    let mut sizes = BTreeMap::new();
    let vectors = info
        .get("result")
        .and_then(|r| r.get("config"))
        .and_then(|c| c.get("params"))
        .and_then(|p| p.get("vectors"));
    let Some(vectors) = vectors.and_then(Value::as_object) else {
        return sizes;
    };
    if let Some(size) = vectors.get("size").and_then(Value::as_i64) {
        sizes.insert("default".to_string(), size);
        return sizes;
    }
    for (name, config) in vectors {
        if let Some(size) = config.get("size").and_then(Value::as_i64) {
            sizes.insert(name.clone(), size);
        }
    }
    sizes
}

/// `local_qdrant.ensure_collection` — existence → size-drift guard → create
/// with `VectorParams(size, Cosine)` + env-gated `_tuning_kwargs`.
/// Python `f"{collection!r}"` wraps in single quotes; collection names are
/// `[A-Za-z0-9_.-]`-validated, so the wrapping is exact.
pub(crate) fn size_drift_message(
    collection: &str,
    sizes: &std::collections::BTreeMap<String, i64>,
    vector_size: usize,
) -> String {
    let actual: Vec<String> = sizes
        .iter()
        .map(|(name, size)| format!("{name}={size}"))
        .collect();
    format!(
        "Qdrant collection '{collection}' has vector size {}, \
         but the configured embedder produces {vector_size}",
        actual.join(", ")
    )
}

pub fn ensure_collection(
    store: &impl VectorWriteOps,
    collection: &str,
    vector_size: usize,
) -> Result<(), String> {
    if store.collection_exists(collection)? {
        let info = store.collection_info(collection)?;
        let sizes = vector_sizes(&info);
        if !sizes.is_empty() && !sizes.values().any(|size| *size as usize == vector_size) {
            return Err(size_drift_message(collection, &sizes, vector_size));
        }
        return Ok(());
    }
    let mut body = Map::new();
    body.insert("vectors".into(), json!({"size": vector_size, "distance": "Cosine"}));
    apply_tuning_kwargs(&mut body)?;
    store.create_collection_body(collection, &Value::Object(body))
}

/// `local_qdrant._tuning_kwargs` — unset env (the default) sends NOTHING.
fn apply_tuning_kwargs(body: &mut Map<String, Value>) -> Result<(), String> {
    let hnsw_m = env_nonempty("QDRANT_HNSW_M");
    let ef_construct = env_nonempty("QDRANT_HNSW_EF_CONSTRUCT");
    let scalar_quant = env_nonempty("QDRANT_SCALAR_QUANT")
        .map(|value| matches!(value.to_lowercase().as_str(), "1" | "true" | "yes" | "on"))
        .unwrap_or(false);
    if hnsw_m.is_some() || ef_construct.is_some() {
        let mut hnsw = Map::new();
        for (key, raw) in [("m", &hnsw_m), ("ef_construct", &ef_construct)] {
            if let Some(raw) = raw {
                let parsed = raw.parse::<i64>().map_err(|_| {
                    format!(
                        "QDRANT_HNSW_M / QDRANT_HNSW_EF_CONSTRUCT must be integers, got \
                         m={:?} ef_construct={:?}",
                        hnsw_m.as_deref().unwrap_or(""),
                        ef_construct.as_deref().unwrap_or("")
                    )
                })?;
                hnsw.insert(key.into(), json!(parsed));
            }
        }
        body.insert("hnsw_config".into(), Value::Object(hnsw));
    }
    if scalar_quant {
        body.insert(
            "quantization_config".into(),
            json!({"scalar": {"type": "int8", "quantile": 0.99, "always_ram": true}}),
        );
    }
    Ok(())
}

/// `_ensure_project_scope_index` — SCOPE_INDEX_FIELDS order is the Python
/// tuple order; the call is idempotent (server-side on remote, engine-level
/// on local since phase-01).
fn ensure_scope_indexes(store: &impl VectorWriteOps, collection: &str) -> Result<(), String> {
    for field in vector_sync::SCOPE_INDEX_FIELDS {
        store.create_payload_index(collection, field)?;
    }
    Ok(())
}

/// One embedding pass over one parser's documents — the
/// `sync_vector_documents` contract: embed everything, ensure collection,
/// index scope fields, upsert (only the LAST batch waits), then delete
/// stale points inside the scope. Returns the document count
/// (`vector_count`). Runs unchanged over the remote REST wire and the local
/// JSON engine via the [`VectorWriteOps`] seam.
#[allow(clippy::too_many_arguments)] // mirrors sync_vector_documents' parameter list 1:1
pub fn sync_vector_documents(
    store: &impl VectorWriteOps,
    embedder: &dyn Embedder,
    collection: &str,
    documents: &[VectorDocument],
    parser: &str,
    project_id: &str,
    root_scope: &str,
    cleanup_paths: &[String],
    full_replace: bool,
) -> Result<usize, String> {
    validate_collection_name(collection)?;
    for document in documents {
        if document.payload.get("project_id").and_then(Value::as_str) != Some(project_id)
            || document.payload.get("parser").and_then(Value::as_str) != Some(parser)
            || document.payload.get("root_scope").and_then(Value::as_str) != Some(root_scope)
        {
            return Err(
                "Vector document payload scope does not match the requested sync scope"
                    .to_string(),
            );
        }
    }
    if !documents.is_empty() {
        let texts: Vec<String> = documents.iter().map(|doc| doc.text.clone()).collect();
        let vectors = embedder
            .embed(&texts)
            .map_err(|error| format!("embedding failed ({}/{} texts): {error}", texts.len(), documents.len()))?;
        if vectors.len() != documents.len() {
            return Err(format!(
                "Embedding output count does not match vector documents ({} vs {})",
                vectors.len(),
                documents.len()
            ));
        }
        let vector_size = vectors[0].len();
        if vector_size == 0 {
            return Err("Embedding model returned empty vectors".to_string());
        }
        ensure_collection(store, collection, vector_size)?;
        ensure_scope_indexes(store, collection)?;
        let points: Vec<Value> = documents
            .iter()
            .zip(vectors.iter())
            .map(|(document, vector)| {
                json!({
                    "id": document.id,
                    "vector": vector.iter().map(|value| *value as f64).collect::<Vec<f64>>(),
                    "payload": document.payload,
                })
            })
            .collect();
        store.upsert_batches(collection, &points)?;
    }
    delete_stale(store, collection, parser, project_id, root_scope, cleanup_paths, documents, full_replace)?;
    store.finalize()?;
    Ok(documents.len())
}

/// `_delete_stale` — filter fields + semantics byte-pinned by the G4 gate.
#[allow(clippy::too_many_arguments)] // mirrors _delete_stale's parameter list 1:1
fn delete_stale(
    store: &impl VectorWriteOps,
    collection: &str,
    parser: &str,
    project_id: &str,
    root_scope: &str,
    cleanup_paths: &[String],
    documents: &[VectorDocument],
    full_replace: bool,
) -> Result<(), String> {
    let keep_ids: Vec<String> = documents.iter().map(|document| document.id.clone()).collect();
    let Some(filter) =
        vector_sync::stale_filter(parser, project_id, root_scope, cleanup_paths, &keep_ids, full_replace)
    else {
        return Ok(());
    };
    if !store.collection_exists(collection)? {
        return Ok(());
    }
    store.delete_by_filter(collection, &filter)?;
    Ok(())
}

/// Model identity for the pass: `--embed-model`/`CODE_EMBEDDING_MODEL`, with
/// the child-level fallback `jinaai/jina-embeddings-v3`
/// (go_analyzer.py:1270 et al.).
pub fn embedding_model_name(embed_model: Option<&str>) -> String {
    embed_model
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .or_else(|| env_nonempty("CODE_EMBEDDING_MODEL"))
        .unwrap_or_else(|| "jinaai/jina-embeddings-v3".to_string())
}

/// Load the pass-wide embedder once (the orchestrator-level win the spike
/// measured: 8.3x cheaper cold start, one model load for every parser).
pub fn pass_embedder() -> Result<Box<dyn Embedder>, String> {
    cortex_embed::embedder_for(Plane::Code)
        .map_err(|error| format!("cortex-embed backend load failed: {error}"))
}

#[cfg(test)]
mod tests {
    /// QDRANT_* env là process-global — các tuning test phải tuần tự.
    fn tuning_env_lock() -> &'static std::sync::Mutex<()> {
        static LOCK: std::sync::OnceLock<std::sync::Mutex<()>> = std::sync::OnceLock::new();
        LOCK.get_or_init(|| std::sync::Mutex::new(()))
    }

    use super::*;

    #[test]
    fn vector_sizes_reads_anonymous_and_named_configs() {
        let anonymous = json!({"result": {"config": {"params": {"vectors": {"size": 1024, "distance": "Cosine"}}}}});
        assert_eq!(vector_sizes(&anonymous).get("default"), Some(&1024));
        let named = json!({"result": {"config": {"params": {"vectors": {"code": {"size": 512}}}}}});
        assert_eq!(vector_sizes(&named).get("code"), Some(&512));
        let missing = json!({"result": {}});
        assert!(vector_sizes(&missing).is_empty());
    }

    #[test]
    fn tuning_body_defaults_to_nothing() {
        let _env_lock = tuning_env_lock().lock().unwrap();
        // No env set on CI machines → body must stay byte-identical to
        // Python's VectorParams-only create. serde_json runs with
        // `preserve_order` workspace-wide, so bytes = INSERTION order —
        // size-first, exactly what the captured Python reference emits
        // (phase06_g4_collection_bodies.json vectors_config).
        unsafe { std::env::remove_var("QDRANT_HNSW_M") };
        unsafe { std::env::remove_var("QDRANT_HNSW_EF_CONSTRUCT") };
        unsafe { std::env::remove_var("QDRANT_SCALAR_QUANT") };
        // Phase-06: repo_root giờ walk-up đúng (marker test) → unit test phải
        // cô lập khỏi config THẬT của repo qua CORTEX_HARNESS_CONFIG_PATH.
        let isolation = tempfile::tempdir().expect("tempdir");
        let previous_config_path = std::env::var("CORTEX_HARNESS_CONFIG_PATH").ok();
        // SAFETY: single-threaded unit test scope.
        unsafe {
            std::env::set_var(
                "CORTEX_HARNESS_CONFIG_PATH",
                isolation.path().join("none.json"),
            );
        }
        let mut body = Map::new();
        body.insert("vectors".into(), json!({"size": 1024, "distance": "Cosine"}));
        apply_tuning_kwargs(&mut body).unwrap();
        assert_eq!(body.len(), 1);
        // Compare as VALUES, not bytes: serde_json Map ordering is a
        // build-graph feature-unification artifact (BTreeMap unless
        // `preserve_order` is unified in, which needs cortex-dev in the
        // same build), and qdrant treats object key order as irrelevant.
        let parsed: Value = serde_json::from_str(&serde_json::to_string(&Value::Object(body)).unwrap()).unwrap();
        assert_eq!(parsed, json!({"vectors": {"size": 1024, "distance": "Cosine"}}));
        // SAFETY: khôi phục env trước khi tempdir drop.
        unsafe {
            match &previous_config_path {
                Some(value) => std::env::set_var("CORTEX_HARNESS_CONFIG_PATH", value),
                None => std::env::remove_var("CORTEX_HARNESS_CONFIG_PATH"),
            }
        }
    }

    #[test]
    fn tuning_body_honours_env() {
        let _env_lock = tuning_env_lock().lock().unwrap();
        unsafe { std::env::set_var("QDRANT_HNSW_M", "24") };
        unsafe { std::env::set_var("QDRANT_SCALAR_QUANT", "on") };
        let mut body = Map::new();
        apply_tuning_kwargs(&mut body).unwrap();
        assert_eq!(body["hnsw_config"], json!({"m": 24}));
        assert_eq!(
            body["quantization_config"],
            json!({"scalar": {"type": "int8", "quantile": 0.99, "always_ram": true}})
        );
        unsafe { std::env::remove_var("QDRANT_HNSW_M") };
        unsafe { std::env::remove_var("QDRANT_SCALAR_QUANT") };
        let mut bad = Map::new();
        unsafe { std::env::set_var("QDRANT_HNSW_M", "not-a-number") };
        assert!(apply_tuning_kwargs(&mut bad).is_err());
        unsafe { std::env::remove_var("QDRANT_HNSW_M") };
    }

    #[test]
    fn model_name_chain_matches_child_defaults() {
        unsafe { std::env::remove_var("CODE_EMBEDDING_MODEL") };
        assert_eq!(embedding_model_name(None), "jinaai/jina-embeddings-v3");
        assert_eq!(embedding_model_name(Some("  ")), "jinaai/jina-embeddings-v3");
        assert_eq!(embedding_model_name(Some("m")), "m");
    }

    #[test]
    fn scope_guard_rejects_foreign_payloads() {
        let store_url = std::env::var("PHASE06_QDRANT_URL").unwrap_or_else(|_| "http://127.0.0.1:1".to_string());
        let Ok(store) = RemoteQdrantStore::new(&store_url, None, None, Some("code")) else {
            return;
        };
        let mut payload = Map::new();
        payload.insert("project_id".into(), json!("other"));
        let document = VectorDocument { id: "x".into(), text: "t".into(), payload };
        let error = sync_vector_documents(
            &store,
            &NullEmbedder,
            "col",
            &[document],
            "go",
            "proj",
            "/r",
            &[],
            false,
        )
        .unwrap_err();
        assert_eq!(error, "Vector document payload scope does not match the requested sync scope");
    }

    // ── phase-02: local seam (native-vector-ingest-local) ──────────────────

    /// CORTEX_VECTOR_BACKEND is process-global — hatch tests serialize on
    /// the same lock as the other env-mutating tests in this module.
    fn backend_env_lock() -> &'static std::sync::Mutex<()> {
        tuning_env_lock()
    }

    struct FixedEmbedder {
        dim: usize,
    }
    impl Embedder for FixedEmbedder {
        fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, cortex_embed::EmbedError> {
            Ok(texts
                .iter()
                .map(|text| {
                    let seed = text.bytes().fold(0u64, |acc, byte| acc.wrapping_mul(31).wrapping_add(byte as u64));
                    (0..self.dim)
                        .map(|dim| ((seed >> (dim % 13)) & 0xff) as f32 / 255.0 + 0.01)
                        .collect::<Vec<f32>>()
                })
                .collect())
        }
        fn dimension(&self) -> Option<usize> {
            None
        }
        fn backend_name(&self) -> &'static str {
            "fixed"
        }
    }

    fn local_test_store(label: &str) -> (tempfile::TempDir, LocalQdrantStore) {
        let isolation = tempfile::tempdir().expect("tempdir");
        let store_root = isolation.path().join("qdrant").join("code");
        let resolved = resolve_storage(
            isolation.path(),
            None,
            &ResolveOverrides {
                data_home: Some(isolation.path().to_string_lossy().into_owned()),
                // Pin the code-store path: parallel hatch tests mutate
                // QDRANT_CODE_PATH process-globally, and the override beats
                // env in resolve_storage's precedence.
                qdrant_code_path: Some(store_root.to_string_lossy().into_owned()),
                ..ResolveOverrides::default()
            },
        )
        .unwrap();
        resolved.ensure_directories().unwrap();
        let store = LocalQdrantStore::open(&resolved, cortex_storage::StorageRole::Code.as_str())
            .unwrap_or_else(|error| panic!("{label}: {error}"));
        (isolation, store)
    }

    fn document(id: &str, file: &str) -> VectorDocument {
        let mut payload = Map::new();
        payload.insert("project_id".into(), json!("proj"));
        // document_from_payload adds the normalized scope field the stale
        // filter matches on (vector_sync.rs:331).
        payload.insert("project_id_normalized".into(), json!("proj"));
        payload.insert("parser".into(), json!("go"));
        payload.insert("root_scope".into(), json!("/r"));
        payload.insert("file_path".into(), json!(file));
        VectorDocument { id: id.into(), text: format!("text of {id} in {file}"), payload }
    }

    fn stored_ids(store: &LocalQdrantStore, collection: &str) -> Vec<String> {
        let mut ids: Vec<String> = store
            .scroll(collection, None, 1000, false, false, None)
            .unwrap()
            .0
            .iter()
            .map(|hit| hit["id"].as_str().unwrap().to_string())
            .collect();
        ids.sort();
        ids
    }

    /// Scratch local sync with the flag ON: full replace upserts the JSON
    /// store with correct points/payload; an incremental rename pass deletes
    /// exactly the renamed file's stale points — never the kept ones (the
    /// phase-01 must_not fix is load-bearing here).
    #[test]
    fn local_seam_full_replace_then_incremental_rename() {
        let (_isolation, store) = local_test_store("local-seam");
        let documents = vec![
            document("id-a", "/r/a.go"),
            document("id-b", "/r/b.go"),
            document("id-c", "/r/c.go"),
        ];
        let count = sync_vector_documents(
            &store,
            &FixedEmbedder { dim: 2 },
            "code",
            &documents,
            "go",
            "proj",
            "/r",
            &[],
            true,
        )
        .unwrap();
        assert_eq!(count, 3);
        assert!(store.collection_exists("code"));
        assert_eq!(stored_ids(&store, "code"), vec!["id-a", "id-b", "id-c"]);

        // Incremental: a.go renamed away → cleanup_paths carries it, keep_ids
        // are the surviving documents.
        let survivors = vec![document("id-b", "/r/b.go"), document("id-c", "/r/c.go")];
        let count = sync_vector_documents(
            &store,
            &FixedEmbedder { dim: 2 },
            "code",
            &survivors,
            "go",
            "proj",
            "/r",
            &["/r/a.go".to_string()],
            false,
        )
        .unwrap();
        assert_eq!(count, 2);
        assert_eq!(stored_ids(&store, "code"), vec!["id-b", "id-c"], "only the stale a.go point dies");

        // Payload shape survived the JSON round-trip.
        let hits = store.scroll("code", None, 10, true, false, None).unwrap().0;
        let payload_b = hits
            .iter()
            .find(|hit| hit["id"] == "id-b")
            .map(|hit| hit["payload"].clone())
            .unwrap();
        assert_eq!(payload_b["file_path"], "/r/b.go");
        assert_eq!(payload_b["project_id"], "proj");
        assert_eq!(payload_b["parser"], "go");

        // Scope indexes exist exactly once each (idempotent engine path).
        let info = store.get_collection_info("code").unwrap();
        let fields: Vec<&str> = info
            .pointer("/result/payload_indexes")
            .unwrap()
            .as_array()
            .unwrap()
            .iter()
            .map(|index| index["field_name"].as_str().unwrap())
            .collect();
        let mut sorted_fields = fields.clone();
        sorted_fields.sort();
        let mut expected: Vec<&str> = vector_sync::SCOPE_INDEX_FIELDS.to_vec();
        expected.sort();
        assert_eq!(sorted_fields, expected, "each scope field indexed exactly once: {fields:?}");
        store.close();
    }

    /// The size-drift guard works on the local store now that
    /// get_collection_info carries the vector config (phase-01 F3b fix).
    #[test]
    fn local_seam_drift_guard_message() {
        let (_isolation, store) = local_test_store("local-drift");
        store
            .create_collection("code", &json!({"vectors": {"size": 2, "distance": "Cosine"}}))
            .unwrap();
        let error = sync_vector_documents(
            &store,
            &FixedEmbedder { dim: 4 },
            "code",
            &[document("id-a", "/r/a.go")],
            "go",
            "proj",
            "/r",
            &[],
            false,
        )
        .unwrap_err();
        assert_eq!(
            error,
            "Qdrant collection 'code' has vector size default=2, \
             but the configured embedder produces 4"
        );
        store.close();
    }

    /// Hatch gate (red-team H2/C1, pre-flip): flag unset → frozen Unsupported
    /// (zero writes, behavior identical to before the plan); `=python` →
    /// frozen too; `=rust` → the local JSON engine opens for business. The
    /// phase-05 flip commit swaps unset to native.
    #[test]
    fn open_native_store_honours_vector_backend_hatch() {
        let _env = backend_env_lock().lock().unwrap();
        let isolation = tempfile::tempdir().expect("tempdir");
        let store_root = isolation.path().join("qdrant").join("code");
        std::fs::create_dir_all(&store_root).unwrap();

        let previous = |key: &str| std::env::var(key).ok();
        let restore = |key: &str, value: Option<String>| {
            // SAFETY: serialized by backend_env_lock; test-only env scope.
            unsafe {
                match value {
                    Some(value) => std::env::set_var(key, value),
                    None => std::env::remove_var(key),
                }
            }
        };
        let saved_project = previous("CORTEX_STORAGE_PROJECT_ID");
        let saved_config = previous("CORTEX_HARNESS_CONFIG_PATH");
        let saved_qdrant = previous("QDRANT_CODE_PATH");
        let saved_backend = previous("CORTEX_VECTOR_BACKEND");
        restore("CORTEX_STORAGE_PROJECT_ID", None);
        // Registry isolation: point the config dir at an empty temp file so
        // registry_backend never sees the repo's real config.
        restore(
            "CORTEX_HARNESS_CONFIG_PATH",
            Some(isolation.path().join("none.json").to_string_lossy().into_owned()),
        );
        restore("QDRANT_CODE_PATH", Some(store_root.to_string_lossy().into_owned()));

        // Unset flag → frozen (byte-identical outcome to pre-plan behavior).
        restore("CORTEX_VECTOR_BACKEND", None);
        match open_native_store(Some(&store_root.to_string_lossy())) {
            NativeStore::Unsupported(reason) => {
                assert!(reason.contains("FROZEN at zero writes"), "honest frozen reason: {reason}");
                assert!(reason.contains("CORTEX_VECTOR_BACKEND=rust"), "escape hatch named: {reason}");
            }
            _ => panic!("unset hatch must stay frozen before the flip"),
        }
        // `=python` → frozen as well.
        restore("CORTEX_VECTOR_BACKEND", Some("python".into()));
        assert!(matches!(open_native_store(Some(&store_root.to_string_lossy())), NativeStore::Unsupported(_)));
        // `=rust` → Local JSON engine; the store file appears after a flush.
        restore("CORTEX_VECTOR_BACKEND", Some("rust".into()));
        match open_native_store(Some(&store_root.to_string_lossy())) {
            NativeStore::Local(store) => {
                store.create_collection("code", &json!({"size": 2, "distance": "Cosine"})).unwrap();
                assert!(store.collection_exists("code"));
                assert!(store_root.join("cortex-local-store.json").exists());
                store.close();
            }
            other => panic!("=rust must resolve the local JSON engine, got {other:?}"),
        }

        restore("CORTEX_STORAGE_PROJECT_ID", saved_project);
        restore("CORTEX_HARNESS_CONFIG_PATH", saved_config);
        restore("QDRANT_CODE_PATH", saved_qdrant);
        restore("CORTEX_VECTOR_BACKEND", saved_backend);
    }

    struct NullEmbedder;
    impl Embedder for NullEmbedder {
        fn embed(&self, _texts: &[String]) -> Result<Vec<Vec<f32>>, cortex_embed::EmbedError> {
            Ok(Vec::new())
        }
        fn dimension(&self) -> Option<usize> {
            None
        }
        fn backend_name(&self) -> &'static str {
            "null"
        }
    }
}

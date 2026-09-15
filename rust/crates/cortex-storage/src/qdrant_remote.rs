//! Qdrant remote-mode adapter for Cortex Harness.
//!
//! Port of `cortex_harness.storage.qdrant_remote` over a blocking HTTP
//! client (`ureq`). Mirrors the public API of [`crate::qdrant::
//! LocalQdrantStore`] so callers can swap a local file backend for a remote
//! server backend without rewriting queries:
//!
//! * The constructor takes a URL (and optional API key) instead of a
//!   resolved filesystem path.
//! * No `StorageLease` is acquired — the server side owns concurrency.
//! * Clients are cached per `(url, api_key)` so projects sharing the same
//!   backend (but with different credentials) do not collide.
//! * Health-check failures raise `BackendConnectionError`.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use serde_json::{json, Value};

use crate::errors::{BackendConnectionError, StoreError, StoreResult};

/// A blocking HTTP client bound to one `(url, api_key)` pair.
#[derive(Clone)]
pub struct RemoteQdrantClient {
    agent: ureq::Agent,
    url: String,
    api_key: Option<String>,
}

impl std::fmt::Debug for RemoteQdrantClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "RemoteQdrantClient(url={:?}, api_key=***)", self.url)
    }
}

impl RemoteQdrantClient {
    fn build(url: &str, api_key: Option<&str>, timeout: Option<f64>) -> Self {
        let timeout = Duration::from_secs_f64(timeout.unwrap_or(30.0));
        let agent = ureq::AgentBuilder::new()
            .timeout_connect(timeout)
            .timeout(timeout)
            .build();
        Self {
            agent,
            url: url.trim_end_matches('/').to_string(),
            api_key: api_key.map(str::to_string),
        }
    }

    fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<&Value>,
    ) -> StoreResult<Value> {
        let endpoint = format!("{}{}", self.url, path);
        let mut request = match method {
            "GET" => self.agent.get(&endpoint),
            "POST" => self.agent.post(&endpoint),
            "PUT" => self.agent.put(&endpoint),
            "DELETE" => self.agent.delete(&endpoint),
            other => {
                return Err(StoreError::Value(format!(
                    "unsupported HTTP method: {other}"
                )))
            }
        };
        request = request.set("Content-Type", "application/json");
        if let Some(api_key) = &self.api_key {
            request = request.set("api-key", api_key);
        }
        let response = match body {
            Some(payload) => request.send_json(payload.clone()),
            None => request.call(),
        };
        match response {
            Ok(response) => {
                let mut raw = String::new();
                use std::io::Read as _;
                response
                    .into_reader()
                    .read_to_string(&mut raw)
                    .map_err(|err| {
                        StoreError::Connection(BackendConnectionError::new(
                            "Qdrant",
                            &self.url,
                            Some(err.to_string()),
                        ))
                })?;
                if raw.is_empty() {
                    Ok(Value::Null)
                } else {
                    serde_json::from_str(&raw).map_err(|err| {
                        StoreError::Value(format!("invalid qdrant response: {err}"))
                    })
                }
            }
            Err(ureq::Error::Status(code, response)) => {
                let mut raw = String::new();
                let _ = response.into_reader().read_to_string(&mut raw);
                Err(StoreError::Value(format!(
                    "qdrant request failed with status {code}: {raw}"
                )))
            }
            Err(ureq::Error::Transport(err)) => Err(StoreError::Connection(
                BackendConnectionError::new("Qdrant", &self.url, Some(err.to_string())),
            )),
        }
    }
}

/// Client cache key: (url, api_key).
type ClientCacheKey = (String, Option<String>);

fn remote_client_cache() -> &'static Mutex<BTreeMap<ClientCacheKey, RemoteQdrantClient>> {
    static CACHE: OnceLock<Mutex<BTreeMap<ClientCacheKey, RemoteQdrantClient>>> =
        OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(BTreeMap::new()))
}

/// Return a cached client for `url`; callers with the same `(url, api_key)`
/// share one connection pool (`get_remote_client`).
pub fn get_remote_client(
    url: &str,
    api_key: Option<&str>,
) -> RemoteQdrantClient {
    let cache_key = (url.to_string(), api_key.map(str::to_string));
    let mut cache = remote_client_cache()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    cache
        .entry(cache_key)
        .or_insert_with(|| RemoteQdrantClient::build(url, api_key, None))
        .clone()
}

/// Close every cached remote client (test-only convenience).
pub fn reset_remote_clients() {
    remote_client_cache()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .clear();
}

/// Single owner of vector operations for one remote Qdrant backend
/// (`RemoteQdrantStore`).
#[derive(Clone, Debug)]
pub struct RemoteQdrantStore {
    url: String,
    api_key: Option<String>,
    role: String,
}

impl RemoteQdrantStore {
    pub fn new(
        url: &str,
        api_key: Option<&str>,
        timeout: Option<f64>,
        role: Option<&str>,
    ) -> StoreResult<Self> {
        // Avoid constructor-time version probes: backend selection and target
        // fingerprinting must complete before the first network operation.
        let _ = RemoteQdrantClient::build(url, api_key, timeout);
        Ok(Self {
            url: url.to_string(),
            api_key: api_key.map(str::to_string),
            role: role.unwrap_or("code").to_string(),
        })
    }

    pub fn role(&self) -> &str {
        &self.role
    }

    pub fn url(&self) -> &str {
        &self.url
    }

    fn client(&self) -> RemoteQdrantClient {
        get_remote_client(&self.url, self.api_key.as_deref())
    }

    // ── collections ─────────────────────────────────────────────────────────

    pub fn list_collection_names(&self) -> StoreResult<Vec<String>> {
        let payload = self.client().request("GET", "/collections", None)?;
        Ok(payload
            .get("result")
            .and_then(|result| result.get("collections"))
            .and_then(Value::as_array)
            .map(|collections| {
                collections
                    .iter()
                    .filter_map(|collection| {
                        collection.get("name").and_then(Value::as_str).map(str::to_string)
                    })
                    .collect()
            })
            .unwrap_or_default())
    }

    pub fn collection_exists(&self, name: &str) -> StoreResult<bool> {
        match self
            .client()
            .request("GET", &format!("/collections/{name}"), None)
        {
            Ok(_) => Ok(true),
            Err(StoreError::Value(message)) if message.contains("status 404") => Ok(false),
            Err(err) => Err(err),
        }
    }

    pub fn get_collection_info(&self, name: &str) -> StoreResult<Value> {
        self.client()
            .request("GET", &format!("/collections/{name}"), None)
    }

    pub fn create_collection(&self, name: &str, vectors_config: &Value) -> StoreResult<()> {
        let body = json!({"vectors": vectors_config});
        self.client()
            .request("PUT", &format!("/collections/{name}"), Some(&body))?;
        Ok(())
    }

    /// Create with the caller-owned full body (phase-06 native embedding
    /// pass mirrors Python `_tuning_kwargs`: optional `hnsw_config` /
    /// `quantization_config` siblings of `vectors`).
    pub fn create_collection_body(&self, name: &str, body: &Value) -> StoreResult<()> {
        self.client()
            .request("PUT", &format!("/collections/{name}"), Some(body))?;
        Ok(())
    }

    pub fn recreate_collection(&self, name: &str, vectors_config: &Value) -> StoreResult<()> {
        let _ = self.delete_collection(name);
        self.create_collection(name, vectors_config)
    }

    pub fn delete_collection(&self, name: &str) -> StoreResult<()> {
        self.client()
            .request("DELETE", &format!("/collections/{name}"), None)?;
        Ok(())
    }

    // ── points ──────────────────────────────────────────────────────────────

    pub fn upsert(&self, collection_name: &str, points: &[Value]) -> StoreResult<Value> {
        self.upsert_wait(collection_name, points, true)
    }

    /// `wait=false` for intermediate batches — the phase-06 native embedding
    /// pass reproduces Python's durability contract exactly: only the final
    /// upsert batch of a sync waits for the flush.
    pub fn upsert_wait(
        &self,
        collection_name: &str,
        points: &[Value],
        wait: bool,
    ) -> StoreResult<Value> {
        let body = json!({"points": points});
        // qdrant upsert is PUT /points; POST /points is the point-retrieve
        // endpoint (expects {"ids": [...]}) and 400s on a points body — the
        // phase-06 native pass caught this against a real server.
        self.client().request(
            "PUT",
            &format!(
                "/collections/{collection_name}/points?wait={}",
                if wait { "true" } else { "false" }
            ),
            Some(&body),
        )
    }

    pub fn upload_points(&self, collection_name: &str, points: &[Value]) -> StoreResult<Value> {
        self.upsert(collection_name, points)
    }

    pub fn search(
        &self,
        collection_name: &str,
        query_vector: &[f64],
        limit: usize,
        query_filter: Option<&Value>,
        with_payload: bool,
        with_vectors: bool,
    ) -> StoreResult<Vec<Value>> {
        let mut body = json!({
            "vector": query_vector,
            "limit": limit,
            "with_payload": with_payload,
            "with_vectors": with_vectors,
        });
        if let Some(filter) = query_filter {
            body["filter"] = filter.clone();
        }
        let payload = self.client().request(
            "POST",
            &format!("/collections/{collection_name}/points/search"),
            Some(&body),
        )?;
        Ok(payload
            .get("result")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default())
    }

    pub fn scroll(
        &self,
        collection_name: &str,
        scroll_filter: Option<&Value>,
        limit: usize,
        with_payload: bool,
        with_vectors: bool,
        offset: Option<&Value>,
    ) -> StoreResult<(Vec<Value>, Option<Value>)> {
        let mut body = json!({
            "limit": limit,
            "with_payload": with_payload,
            "with_vectors": with_vectors,
        });
        if let Some(filter) = scroll_filter {
            body["filter"] = filter.clone();
        }
        if let Some(offset) = offset {
            body["offset"] = offset.clone();
        }
        let payload = self.client().request(
            "POST",
            &format!("/collections/{collection_name}/points/scroll"),
            Some(&body),
        )?;
        let result = payload.get("result").cloned().unwrap_or(Value::Null);
        let points = result
            .get("points")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        Ok((points, result.get("next_page_offset").cloned()))
    }

    pub fn retrieve(
        &self,
        collection_name: &str,
        ids: &[Value],
        with_payload: bool,
        with_vectors: bool,
    ) -> StoreResult<Vec<Value>> {
        let body = json!({
            "ids": ids,
            "with_payload": with_payload,
            "with_vectors": with_vectors,
        });
        let payload = self.client().request(
            "POST",
            &format!("/collections/{collection_name}/points"),
            Some(&body),
        )?;
        Ok(payload
            .get("result")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default())
    }

    pub fn count(&self, collection_name: &str, count_filter: Option<&Value>) -> StoreResult<i64> {
        let mut body = json!({"exact": true});
        if let Some(filter) = count_filter {
            body["filter"] = filter.clone();
        }
        let payload = self.client().request(
            "POST",
            &format!("/collections/{collection_name}/points/count"),
            Some(&body),
        )?;
        Ok(payload
            .get("result")
            .and_then(|result| result.get("count"))
            .and_then(Value::as_i64)
            .unwrap_or_default())
    }

    pub fn delete(
        &self,
        collection_name: &str,
        points_selector_ids: Option<&[Value]>,
        filter_selector: Option<&Value>,
    ) -> StoreResult<Value> {
        if points_selector_ids.is_none() && filter_selector.is_none() {
            return Err(StoreError::Value(
                "delete requires point IDs or a filter selector".to_string(),
            ));
        }
        // qdrant REST `PointsSelector` body: {"points": [...]} or
        // {"filter": {...}} — the filter is sent directly, matching what the
        // Python qdrant_client serializes for `filter_selector=FilterSelector`.
        let body = match points_selector_ids {
            Some(ids) => json!({"points": ids}),
            None => json!({"filter": filter_selector.unwrap_or(&Value::Null)}),
        };
        self.client().request(
            "POST",
            &format!("/collections/{collection_name}/points/delete?wait=true"),
            Some(&body),
        )
    }

    pub fn set_payload(
        &self,
        collection_name: &str,
        payload: &Value,
        points: Option<&[Value]>,
        filter: Option<&Value>,
    ) -> StoreResult<Value> {
        if points.is_none() && filter.is_none() {
            return Err(StoreError::Value(
                "set_payload requires point IDs or a filter".to_string(),
            ));
        }
        self.payload_request(collection_name, payload, points, filter)
    }

    pub fn overwrite_payload(
        &self,
        collection_name: &str,
        payload: &Value,
        points: Option<&[Value]>,
        filter: Option<&Value>,
    ) -> StoreResult<Value> {
        if points.is_none() && filter.is_none() {
            return Err(StoreError::Value(
                "overwrite_payload requires point IDs or a filter".to_string(),
            ));
        }
        self.payload_request(collection_name, payload, points, filter)
    }

    fn payload_request(
        &self,
        collection_name: &str,
        payload: &Value,
        points: Option<&[Value]>,
        filter: Option<&Value>,
    ) -> StoreResult<Value> {
        let selector = match points {
            Some(ids) => json!(ids),
            None => json!(filter),
        };
        let body = json!({"payload": payload, "points": selector});
        self.client().request(
            "POST",
            &format!("/collections/{collection_name}/points/payload?wait=true"),
            Some(&body),
        )
    }

    pub fn create_payload_index(
        &self,
        collection_name: &str,
        field_name: &str,
        field_schema: Option<&Value>,
    ) -> StoreResult<()> {
        let body =
            json!({"field_name": field_name, "field_schema": field_schema.unwrap_or(&json!("keyword"))});
        self.client().request(
            "PUT",
            &format!("/collections/{collection_name}/index"),
            Some(&body),
        )?;
        Ok(())
    }

    // ── lifecycle ───────────────────────────────────────────────────────────

    /// Return `true` when the remote server is reachable. Never raises.
    pub fn check_connection(&self) -> bool {
        self.client().request("GET", "/collections", None).is_ok()
    }

    /// Raise `BackendConnectionError` if the server is not reachable
    /// (`ensure_reachable`).
    pub fn ensure_reachable(&self) -> StoreResult<()> {
        self.client().request("GET", "/collections", None)?;
        Ok(())
    }

    /// Drop *this* store's client from the cache (`close`).
    pub fn close(&self) {
        let mut cache = remote_client_cache()
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        cache.remove(&(self.url.clone(), self.api_key.clone()));
    }
}

/// Set of collection names present on a remote server — convenience used by
/// doctor checks.
pub fn remote_collections(url: &str, api_key: Option<&str>) -> StoreResult<BTreeSet<String>> {
    let store = RemoteQdrantStore::new(url, api_key, None, None)?;
    Ok(store.list_collection_names()?.into_iter().collect())
}

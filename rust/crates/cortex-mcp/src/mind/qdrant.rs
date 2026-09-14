//! Qdrant access for the mind tools — port of the `get_qdrant` +
//! `qdrant_search_entity_payload` plumbing of `doc-tiny/mcp_graph_rag.py`.
//!
//! * Remote backend (`storage_backend: remote` + `remote.qdrant_url`):
//!   blocking REST client over `ureq`, mirroring
//!   `cortex_storage::qdrant_remote::RemoteQdrantStore` (same endpoints,
//!   same JSON shapes as the Python `qdrant-client` HTTP layer).
//! * Local backend (python `QdrantClient(path=...)` embedded mode): the
//!   embedded engine has no wire protocol — collection *listing* is served
//!   by scanning the local store directory layout
//!   (`<qdrant_doc>/collection/<name>`); *search* against a non-empty local
//!   store is a Python-plane operation and returns a connection error
//!   envelope (the phase-13 fixtures pin the remote backend; documented
//!   exclusion in the parity report).

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use serde_json::{json, Map, Value};

/// Exception-shaped error mirroring what the Python tool body would raise.
/// `name` is the exception class name (`_standard_tool` maps it through
/// `normalize_error`).
#[derive(Debug, Clone)]
pub struct QdrantError {
    pub exception: &'static str,
    pub message: String,
}

impl QdrantError {
    pub fn lookup(message: impl Into<String>) -> Self {
        Self { exception: "LookupError", message: message.into() }
    }

    pub fn runtime(message: impl Into<String>) -> Self {
        Self { exception: "RuntimeError", message: message.into() }
    }

    pub fn project_not_registered(message: impl Into<String>) -> Self {
        Self { exception: "ProjectNotRegisteredError", message: message.into() }
    }
}

/// Resolved qdrant backend for one request (`get_qdrant(project_id)`).
pub enum QdrantBackend {
    Remote { url: String, api_key: Option<String> },
    Local { doc_path: PathBuf },
}

/// `remote` config keys of the project registry (subset used here).
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

/// Storage root for the *local* fallback — same env chain as
/// `cortex_harness.storage.resolve_storage` for the fixture scenarios:
/// `CORTEX_DATA_HOME` env (absolute), else `~/.cortext-harness`.
fn local_data_root() -> PathBuf {
    match std::env::var("CORTEX_DATA_HOME") {
        Ok(home) if !home.trim().is_empty() => PathBuf::from(home.trim().to_string()),
        _ => match std::env::var("HOME") {
            Ok(home) if !home.is_empty() => PathBuf::from(home).join(".cortext-harness"),
            _ => PathBuf::from(".cortext-harness"),
        },
    }
}

fn local_instance_id() -> String {
    match std::env::var("CORTEX_STORAGE_INSTANCE") {
        Ok(value) if !value.trim().is_empty() => value.trim().to_string(),
        _ => "default".to_string(),
    }
}

/// Local doc-store path (`resolve_storage(cwd).qdrant_doc_path`).
pub fn local_doc_store_path() -> PathBuf {
    local_data_root()
        .join("v1")
        .join("instances")
        .join(local_instance_id())
        .join("qdrant")
        .join("doc")
}

/// Resolve the qdrant backend for `project_id` (`get_qdrant`).
///
/// Reference behavior (`mcp_graph_rag.get_qdrant`, verified against the live
/// server): a project that is not EXACTLY registered (casefold) raises
/// `ProjectNotRegisteredError`. The in-function fallback to the instance
/// store is dead code in Python because `get_qdrant` catches doc-tiny's
/// `project_contract.ProjectNotRegisteredError` while the registry used by
/// `get_document_qdrant_store` raises the code-tiny registry's same-named
/// class — see the parity report (suspected shared bug). Registered projects
/// route through `storage_backend`/`remote.qdrant_url`; a `None`/blank id
/// resolves the (isolated) local instance store.
pub fn resolve_backend(project_id: Option<&str>) -> Result<QdrantBackend, QdrantError> {
    match project_id.map(str::trim).filter(|value| !value.is_empty()) {
        None => Ok(QdrantBackend::Local { doc_path: local_doc_store_path() }),
        Some(project_id) => {
            match crate::project_registry::resolve_project_targets(Some(project_id), None) {
                Ok(targets) => {
                    if targets.storage_backend == "remote"
                        && let Some((url, api_key)) =
                            remote_qdrant_url(targets.remote_config.as_ref())
                    {
                        return Ok(QdrantBackend::Remote { url, api_key });
                    }
                    Ok(QdrantBackend::Local { doc_path: local_doc_store_path() })
                }
                Err(error) => Err(QdrantError {
                    exception: "ProjectNotRegisteredError",
                    message: error.to_string(),
                }),
            }
        }
    }
}

// ---------------------------------------------------------------------------
// REST client (ureq) — mirrors cortex_storage::qdrant_remote
// ---------------------------------------------------------------------------

struct RemoteClient {
    agent: ureq::Agent,
    url: String,
    api_key: Option<String>,
}

impl RemoteClient {
    fn request(&self, method: &str, path: &str, body: Option<&Value>) -> Result<Value, QdrantError> {
        let endpoint = format!("{}{}", self.url, path);
        let mut request = match method {
            "GET" => self.agent.get(&endpoint),
            "POST" => self.agent.post(&endpoint),
            "PUT" => self.agent.put(&endpoint),
            "DELETE" => self.agent.delete(&endpoint),
            other => return Err(QdrantError::runtime(format!("unsupported HTTP method: {other}"))),
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
                response.into_reader().read_to_string(&mut raw).map_err(|err| {
                    QdrantError::runtime(format!("qdrant connection failed: {err}"))
                })?;
                if raw.is_empty() {
                    Ok(Value::Null)
                } else {
                    serde_json::from_str(&raw)
                        .map_err(|err| QdrantError::runtime(format!("invalid qdrant response: {err}")))
                }
            }
            Err(ureq::Error::Status(code, response)) => {
                let mut raw = String::new();
                let _ = response.into_reader().read_to_string(&mut raw);
                Err(QdrantError::runtime(format!(
                    "qdrant request failed with status {code}: {raw}"
                )))
            }
            Err(ureq::Error::Transport(err)) => Err(QdrantError::runtime(format!(
                "qdrant connection failed to {}: {err}",
                self.url
            ))),
        }
    }
}

fn remote_client(url: &str, api_key: Option<&str>) -> RemoteClient {
    type ClientCache = Mutex<BTreeMap<(String, Option<String>), RemoteClient>>;
    static CACHE: OnceLock<ClientCache> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(BTreeMap::new()));
    let mut guard = cache.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let key = (url.to_string(), api_key.map(str::to_string));
    guard
        .entry(key)
        .or_insert_with(|| {
            let agent = ureq::AgentBuilder::new()
                .timeout_connect(Duration::from_secs(30))
                .timeout(Duration::from_secs(30))
                .build();
            RemoteClient { agent, url: url.trim_end_matches('/').to_string(), api_key: api_key.map(str::to_string) }
        })
        .clone()
}

impl Clone for RemoteClient {
    fn clone(&self) -> Self {
        Self {
            agent: self.agent.clone(),
            url: self.url.clone(),
            api_key: self.api_key.clone(),
        }
    }
}

/// `list_collection_names()` — remote REST or local directory scan.
pub fn list_collection_names(backend: &QdrantBackend) -> Result<Vec<String>, QdrantError> {
    match backend {
        QdrantBackend::Remote { url, api_key } => {
            let payload = remote_client(url, api_key.as_deref()).request("GET", "/collections", None)?;
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
        QdrantBackend::Local { doc_path } => {
            // Local-mode store layout: `<path>/collection/<name>` directories
            // (name-sorted, matching `QdrantClient.get_collections()`).
            let base = doc_path.join("collection");
            let Ok(entries) = std::fs::read_dir(&base) else {
                return Ok(Vec::new());
            };
            let mut names: Vec<String> = entries
                .flatten()
                .map(|entry| entry.path())
                .filter(|path| path.is_dir())
                .filter_map(|path| path.file_name().map(|name| name.to_string_lossy().to_string()))
                .collect();
            names.sort();
            Ok(names)
        }
    }
}

/// One qdrant search hit (`hit.score` / `hit.payload`).
pub struct SearchHit {
    pub score: f64,
    pub payload: Map<String, Value>,
}

/// Vector search on one collection — POST `/points/search` (remote only).
pub fn search_points(
    backend: &QdrantBackend,
    collection_name: &str,
    query_vector: &[f64],
    limit: usize,
    query_filter: Option<&Value>,
) -> Result<Vec<SearchHit>, QdrantError> {
    let (url, api_key) = match backend {
        QdrantBackend::Remote { url, api_key } => (url.clone(), api_key.clone()),
        QdrantBackend::Local { .. } => {
            // Local embedded search is python-plane (no wire protocol).
            return Err(QdrantError::runtime(
                "Local Qdrant vector search is not available in the Rust runtime; \
                 configure a remote qdrant_url for this project.",
            ));
        }
    };
    let mut body = json!({
        "vector": query_vector,
        "limit": limit,
        "with_payload": true,
        "with_vectors": false,
    });
    if let Some(filter) = query_filter {
        body["filter"] = filter.clone();
    }
    let payload = remote_client(&url, api_key.as_deref()).request(
        "POST",
        &format!("/collections/{collection_name}/points/search"),
        Some(&body),
    )?;
    Ok(payload
        .get("result")
        .and_then(Value::as_array)
        .map(|hits| {
            hits.iter()
                .filter_map(|hit| {
                    let score = hit.get("score").and_then(Value::as_f64)?;
                    let payload = hit.get("payload").and_then(Value::as_object).cloned()?;
                    Some(SearchHit { score, payload })
                })
                .collect()
        })
        .unwrap_or_default())
}

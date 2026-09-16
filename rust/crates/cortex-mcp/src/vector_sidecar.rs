//! Local-Qdrant vector sidecar (vector-lane plan phase-04,
//! `plans/260915-2027-vector-lane-rust-port`).
//!
//! The python `qdrant-client` local mode persists points as pickled
//! `PointStruct` rows in per-collection SQLite files — an internal format
//! Rust must not parse, and ingest stays Python (spike `260914-1706`
//! phase-05 NO-GO). This module talks NDJSON stdio to
//! `scripts/rust_mcp/vector_worker.py`, which opens the same store with the
//! real client: zero format risk, zero drift, no torch in the worker.
//!
//! One worker per store path, kept process-wide and respawned once on
//! protocol failure (same rollback shape as [`crate::mind::embed`]).

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::{Mutex, OnceLock};

use serde_json::{json, Value};

/// NDJSON request with `expect` semantics — one line in, one line out.
struct VectorWorker {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

fn worker_path() -> Option<PathBuf> {
    worker_path_from(std::env::var("CORTEX_MCP_VECTOR_WORKER").ok())
}

fn worker_path_from(env_override: Option<String>) -> Option<PathBuf> {
    if let Some(path) = env_override {
        let path = PathBuf::from(path);
        return path.is_file().then_some(path);
    }
    // CARGO_MANIFEST_DIR = <repo>/rust/crates/cortex-mcp → repo root 3 up.
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .map(Path::to_path_buf)?;
    let path = repo.join("scripts").join("rust_mcp").join("vector_worker.py");
    path.is_file().then_some(path)
}

fn python_binary() -> String {
    if let Ok(path) = std::env::var("CORTEX_MCP_PYTHON")
        && !path.trim().is_empty()
    {
        return path;
    }
    for candidate in [".venv/bin/python", "venv/bin/python"] {
        if Path::new(candidate).exists() {
            return candidate.to_string();
        }
    }
    "python3".to_string()
}

fn spawn(store_path: &Path) -> Result<VectorWorker, String> {
    let script = worker_path()
        .ok_or_else(|| "vector worker script not found (CORTEX_MCP_VECTOR_WORKER)".to_string())?;
    let mut child = Command::new(python_binary())
        .arg(&script)
        .arg("--store")
        .arg(store_path)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("vector worker spawn failed: {error}"))?;
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "vector worker stdin unavailable".to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "vector worker stdout unavailable".to_string())?;
    Ok(VectorWorker {
        child,
        stdin,
        stdout: BufReader::new(stdout),
    })
}

impl VectorWorker {
    fn request(&mut self, payload: &Value) -> Result<Value, String> {
        let line = serde_json::to_string(payload).map_err(|error| error.to_string())?;
        writeln!(self.stdin, "{line}").map_err(|error| format!("vector worker write: {error}"))?;
        self.stdin
            .flush()
            .map_err(|error| format!("vector worker flush: {error}"))?;
        let mut response = String::new();
        let read = self
            .stdout
            .read_line(&mut response)
            .map_err(|error| format!("vector worker read: {error}"))?;
        if read == 0 {
            return Err("vector worker closed the protocol stream".to_string());
        }
        serde_json::from_str(response.trim())
            .map_err(|error| format!("vector worker protocol: {error}"))
    }
}

impl Drop for VectorWorker {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn worker_cache() -> &'static Mutex<HashMap<PathBuf, Option<VectorWorker>>> {
    static CACHE: OnceLock<Mutex<HashMap<PathBuf, Option<VectorWorker>>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn run_request(
    store_path: &Path,
    payload: &Value,
) -> Result<Value, String> {
    if let Some(refusal) = quarantined_store_refusal(store_path) {
        return Err(refusal);
    }
    let mut cache = worker_cache()
        .lock()
        .map_err(|_| "vector worker cache poisoned".to_string())?;
    for attempt in 0..2 {
        if cache.get(store_path).map(Option::is_none).unwrap_or(true) {
            cache.insert(store_path.to_path_buf(), Some(spawn(store_path)?));
        }
        let worker = cache
            .get_mut(store_path)
            .and_then(Option::as_mut)
            .ok_or_else(|| "vector worker unavailable".to_string())?;
        match worker.request(payload) {
            Ok(response) => return Ok(response),
            Err(error) => {
                // Respawn once; a broken pipe mid-request is the expected
                // failure mode after an idle worker is reaped by the OS.
                cache.insert(store_path.to_path_buf(), None);
                if attempt > 0 {
                    return Err(error);
                }
            }
        }
    }
    Err("vector worker unreachable".to_string())
}

/// Rollback guard (native-vector-ingest-local phase-05, red-team H5): a
/// python sidecar aimed at a root whose data has moved to the native JSON
/// store (`cortex-local-store.json` present, legacy pickle `collection/`
/// quarantined away) must refuse LOUDLY — qdrant-client would otherwise
/// re-initialise an empty pickle store and silently serve nothing. Mind/doc
/// stores are pickle-only (no JSON store) and never trip this.
fn quarantined_store_refusal(store_path: &Path) -> Option<String> {
    let json_owned = store_path.join("cortex-local-store.json").is_file();
    let pickle_gone = !store_path.join("collection").exists();
    if json_owned && pickle_gone {
        return Some(format!(
            "python vector sidecar refuses {}: the native JSON vector store owns this root and \
             the legacy pickle collection/ subtree is quarantined — the code-lane data lives in \
             the JSON store. Rerun with CORTEX_VECTOR_BACKEND=rust (native reader) or restore \
             the pickle subtree; serving this root as empty would be a lie.",
            store_path.display()
        ));
    }
    None
}

fn expect_ok(response: Value) -> Result<Value, String> {
    if response.get("ok").and_then(Value::as_bool).unwrap_or(false) {
        Ok(response)
    } else {
        Err(response
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("vector worker error")
            .to_string())
    }
}

/// `list_collections` — collection names in the local store.
pub fn list_collections(store_path: &Path) -> Result<Vec<String>, String> {
    let response = expect_ok(run_request(
        store_path,
        &json!({"op": "list"}),
    )?)?;
    let names = response
        .get("collections")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    Ok(names
        .iter()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect())
}

/// `vector_sizes` — `{"default": 1024}` or named-vector size map.
pub fn collection_vector_sizes(
    store_path: &Path,
    collection: &str,
) -> Result<serde_json::Map<String, Value>, String> {
    let response = expect_ok(run_request(
        store_path,
        &json!({"op": "meta", "collection": collection}),
    )?)?;
    let sizes = response
        .get("sizes")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    Ok(sizes)
}

/// One raw hit object: `{id, version, score, payload}`.
pub fn search(
    store_path: &Path,
    collection: &str,
    vector: &[f64],
    limit: usize,
    filter: Option<&Value>,
) -> Result<Vec<Value>, String> {
    search_with_using(store_path, collection, vector, None, limit, filter)
}

/// Named-vector variant — `using` mirrors python `query_points(using=…)`.
pub fn search_with_using(
    store_path: &Path,
    collection: &str,
    vector: &[f64],
    using: Option<&str>,
    limit: usize,
    filter: Option<&Value>,
) -> Result<Vec<Value>, String> {
    let response = expect_ok(run_request(
        store_path,
        &json!({
            "op": "search",
            "collection": collection,
            "vector": vector,
            "limit": limit,
            "filter": filter,
            "using": using,
        }),
    )?)?;
    Ok(response
        .get("hits")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Post-quarantine rollback (drill leg B): JSON store present + pickle
    /// `collection/` gone → every sidecar op refuses loudly, never serves an
    /// empty store.
    #[test]
    fn quarantined_store_is_refused_loudly() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("cortex-local-store.json"), "{\"collections\":{}}").unwrap();
        let error = list_collections(dir.path()).unwrap_err();
        assert!(
            error.contains("refuses") && error.contains("JSON vector store"),
            "loud quarantine refusal, got: {error}"
        );

        // Mind/doc lane worker-override contract: an EMPTY
        // CORTEX_MCP_VECTOR_WORKER must fail closed (no default fallback) —
        // pure-function check, no python needed.
        std::fs::create_dir_all(dir.path().join("collection").join("docs")).unwrap();
        assert!(
            worker_path_from(Some(String::new())).is_none(),
            "empty CORTEX_MCP_VECTOR_WORKER must fail closed"
        );
    }
}

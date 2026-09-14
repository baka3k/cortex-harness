//! Query embedding for the mind tools — **hai backend sau một seam**, chọn bằng
//! `CORTEX_EMBED_BACKEND=python|onnx` (plans/260914-1706-onnx-embedding-spike
//! phase-02):
//!
//! - `python` (mặc định = Plan B phase-13, giữ nguyên để rollback tức thì):
//!   bge-m3 vẫn là Python sidecar; `torch`/sentence-transformers không vào Rust.
//! - `onnx`: `cortex_embed::OnnxEmbedder` (ort, CPU) — hết spawn worker.
//!
//! `python` dùng worker **persistent** (`scripts/rust_mcp/embed_worker.py`): spawn
//! một lần, NDJSON qua stdin/stdout, để chi phí load model trải trên nhiều query —
//! điều kiện của gate P95. `onnx` cache session trong `OnceLock` vì cùng lý do
//! (load lại 2.27GB mỗi query là không thể đạt gate). Model/device resolution
//! mirror `doc-tiny/embedding_utils.py` + `mcp_graph_rag.get_embedder`.

use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::{Mutex, OnceLock};

use cortex_embed::{Backend, Embedder, OnnxEmbedder, Plane, spec_from_env};

/// A held sidecar process with a serialized request/response channel.
struct EmbedWorker {
    stdin: ChildStdin,
    child: Child,
    /// Response lines pushed by the reader thread (one per request).
    responses: std::sync::mpsc::Receiver<String>,
}

impl Drop for EmbedWorker {
    fn drop(&mut self) {
        // Closing stdin signals EOF → the worker exits.
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn worker_path() -> Option<std::path::PathBuf> {
    if let Ok(path) = std::env::var("CORTEX_MCP_EMBED_WORKER")
        && !path.trim().is_empty()
    {
        let path = std::path::PathBuf::from(path);
        return path.is_file().then_some(path);
    }
    // CARGO_MANIFEST_DIR = <repo>/rust/crates/cortex-mcp → repo root 3 up.
    let repo = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(std::path::Path::parent)
        .and_then(std::path::Path::parent)
        .map(std::path::Path::to_path_buf)?;
    let path = repo.join("scripts").join("rust_mcp").join("embed_worker.py");
    path.is_file().then_some(path)
}

fn python_binary() -> String {
    if let Ok(path) = std::env::var("CORTEX_MCP_PYTHON")
        && !path.trim().is_empty()
    {
        return path;
    }
    for candidate in [".venv/bin/python", "venv/bin/python"] {
        if std::path::Path::new(candidate).exists() {
            return candidate.to_string();
        }
    }
    "python3".to_string()
}

fn spawn_worker() -> Result<EmbedWorker, String> {
    let path = worker_path()
        .ok_or_else(|| "embed worker script not found (CORTEX_MCP_EMBED_WORKER)".to_string())?;
    let mut child = Command::new(python_binary())
        .arg(&path)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("spawn embed worker: {error}"))?;
    let stdin = child.stdin.take().ok_or("embed worker stdin")?;
    let stdout: ChildStdout = child.stdout.take().ok_or("embed worker stdout")?;
    let (tx, rx) = std::sync::mpsc::channel::<String>();
    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            match line {
                Ok(line) => {
                    if tx.send(line).is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    });
    Ok(EmbedWorker { stdin, child, responses: rx })
}

fn worker() -> Result<&'static Mutex<Option<EmbedWorker>>, String> {
    static WORKER: OnceLock<Mutex<Option<EmbedWorker>>> = OnceLock::new();
    let holder = WORKER.get_or_init(|| Mutex::new(None));
    Ok(holder)
}

/// `embedder.encode([query])[0].tolist()` — one query vector, qua backend đang
/// được `CORTEX_EMBED_BACKEND` chọn.
pub fn encode_query(query: &str) -> Result<Vec<f64>, String> {
    match Backend::from_env() {
        Backend::Onnx => encode_query_onnx(query),
        Backend::Python => encode_query_worker(query),
    }
}

/// Đường ONNX native: session dựng một lần rồi giữ cho cả process.
fn encode_query_onnx(query: &str) -> Result<Vec<f64>, String> {
    static EMBEDDER: OnceLock<Mutex<Option<OnnxEmbedder>>> = OnceLock::new();
    if let Some(note) = Backend::device_note(Plane::Doc) {
        eprintln!("[mind.embed] {note}");
    }
    let trace = cortex_embed::trace_enabled();
    let lock_started = std::time::Instant::now();
    let holder = EMBEDDER.get_or_init(|| Mutex::new(None));
    let mut guard = holder.lock().map_err(|_| "embedder lock poisoned".to_string())?;
    let lock_ms = lock_started.elapsed().as_secs_f64() * 1000.0;
    if guard.is_none() {
        let spec = spec_from_env(Plane::Doc).map_err(|error| error.to_string())?;
        let embedder = OnnxEmbedder::new(spec).map_err(|error| error.to_string())?;
        eprintln!("[mind.embed] onnx backend loaded model={}", embedder.spec().id);
        *guard = Some(embedder);
    }
    let embedder = guard.as_ref().ok_or("onnx embedder unavailable")?;
    let embed_started = std::time::Instant::now();
    let vectors = embedder
        .embed(&[query.to_string()])
        .map_err(|error| error.to_string())?;
    if trace {
        eprintln!(
            "[mind.embed.trace] lock_wait={lock_ms:.1}ms embed={:.1}ms",
            embed_started.elapsed().as_secs_f64() * 1000.0
        );
    }
    vectors
        .into_iter()
        .next()
        .map(|vector| vector.into_iter().map(f64::from).collect())
        .ok_or_else(|| "onnx embedder returned no vector".to_string())
}

fn encode_query_worker(query: &str) -> Result<Vec<f64>, String> {
    let holder = worker()?;
    let mut guard = holder.lock().map_err(|_| "embed worker lock poisoned")?;
    if guard.is_none() {
        *guard = Some(spawn_worker()?);
    }
    let request = serde_json::json!({ "texts": [query] });
    let mut respawned = false;
    let response_line = loop {
        {
            let worker = guard.as_mut().expect("spawned");
            let line = serde_json::to_string(&request).map_err(|e| e.to_string())?;
            let write = writeln!(worker.stdin, "{line}")
                .and_then(|_| worker.stdin.flush());
            if write.is_err() {
                // Broken pipe → worker died; respawn once.
                if respawned {
                    return Err("embed worker died and did not restart".to_string());
                }
                respawned = true;
                let _ = worker.child.kill();
                let _ = worker.child.wait();
                *guard = Some(spawn_worker()?);
                continue;
            }
        }
        let worker = guard.as_mut().expect("spawned");
        match worker.responses.recv_timeout(std::time::Duration::from_secs(120)) {
            Ok(line) => {
                if line.starts_with('{') {
                    break line;
                }
                // Startup noise on stdout — ignore and keep waiting.
            }
            Err(_) => {
                if respawned {
                    return Err("embed worker timed out".to_string());
                }
                respawned = true;
                let _ = worker.child.kill();
                let _ = worker.child.wait();
                *guard = Some(spawn_worker()?);
            }
        }
    };
    let payload: serde_json::Value =
        serde_json::from_str(&response_line).map_err(|error| format!("embed worker output: {error}"))?;
    let vector = payload
        .get("vectors")
        .and_then(serde_json::Value::as_array)
        .and_then(|rows| rows.first())
        .and_then(|row| row.as_array())
        .map(|nums| nums.iter().filter_map(|value| value.as_f64()).collect::<Vec<f64>>())
        .ok_or_else(|| format!("embed worker returned no vector: {response_line}"))?;
    Ok(vector)
}

//! `SidecarEmbedder`: giữ nguyên đường Python hiện tại (worker NDJSON persistent)
//! để A/B với `OnnxEmbedder` và làm rollback mặc định.
//!
//! Protocol khớp `scripts/rust_mcp/embed_worker.py` (request `texts`/`model`/
//! `device`, response `dimension`/`vectors`) và khớp cách `cortex-mcp` đang spawn
//! worker: một process sống lâu, model load một lần, respawn tối đa một lần khi
//! broken pipe hoặc timeout.

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::sync::Mutex;
use std::time::Duration;

use crate::backend::Embedder;
use crate::error::{EmbedError, Result};
use crate::model::Plane;

const REQUEST_TIMEOUT: Duration = Duration::from_secs(120);

pub struct SidecarEmbedder {
    plane: Plane,
    model: String,
    worker: Mutex<Option<Worker>>,
    dimension: Mutex<Option<usize>>,
}

struct Worker {
    stdin: ChildStdin,
    child: Child,
    responses: Receiver<String>,
}

impl Drop for Worker {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl SidecarEmbedder {
    pub fn new(plane: Plane, model: impl Into<String>) -> Self {
        Self {
            plane,
            model: model.into(),
            worker: Mutex::new(None),
            dimension: Mutex::new(None),
        }
    }

    /// Model mà worker sẽ load ở request đầu tiên (chuỗi env của plane).
    #[must_use]
    pub fn model(&self) -> &str {
        &self.model
    }
}

fn repo_root_from_manifest() -> PathBuf {
    // CARGO_MANIFEST_DIR = <repo>/rust/crates/cortex-embed -> repo root 3 up.
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(std::path::Path::parent)
        .and_then(std::path::Path::parent)
        .map(PathBuf::from)
        .unwrap_or_else(crate::model::repo_root)
}

fn worker_script() -> Result<PathBuf> {
    let candidate = match crate::backend::read_env("CORTEX_MCP_EMBED_WORKER") {
        Some(explicit) => PathBuf::from(explicit),
        None => repo_root_from_manifest()
            .join("scripts")
            .join("rust_mcp")
            .join("embed_worker.py"),
    };
    if candidate.is_file() {
        return Ok(candidate);
    }
    Err(EmbedError::new(format!(
        "embed worker script not found: {} (set CORTEX_MCP_EMBED_WORKER)",
        candidate.display()
    )))
}

fn python_binary() -> String {
    for key in ["CORTEX_EMBED_PYTHON", "CORTEX_MCP_PYTHON", "CORTEX_DOC_PYTHON"] {
        if let Some(path) = crate::backend::read_env(key) {
            return path;
        }
    }
    let root = repo_root_from_manifest();
    for candidate in [root.join(".venv/bin/python"), root.join("venv/bin/python")] {
        if candidate.is_file() {
            return candidate.to_string_lossy().into_owned();
        }
    }
    "python3".to_string()
}

impl SidecarEmbedder {
    fn request(&self, texts: &[String]) -> String {
        let mut payload = serde_json::json!({ "texts": texts });
        // `embed_worker.py` chỉ đọc model/device ở request đầu tiên (model giữ
        // trong process state) — gửi kèm để chuỗi env của plane được tôn trọng.
        if !self.model.is_empty() {
            payload["model"] = serde_json::Value::String(self.model.clone());
        }
        payload["device"] = serde_json::Value::String("cpu".to_string());
        payload.to_string()
    }

    fn parse_response(&self, line: &str) -> Result<Vec<Vec<f32>>> {
        let payload: serde_json::Value = serde_json::from_str(line)?;
        if let Some(error) = payload.get("error").and_then(serde_json::Value::as_str) {
            return Err(EmbedError::new(format!("embed worker error: {error}")));
        }
        let rows = payload
            .get("vectors")
            .and_then(serde_json::Value::as_array)
            .ok_or_else(|| {
                EmbedError::new(format!("embed worker returned no vectors: {line}"))
            })?;
        let vectors = rows
            .iter()
            .map(|row| {
                row.as_array()
                    .map(|nums| {
                        nums.iter()
                            .filter_map(|value| value.as_f64().map(|v| v as f32))
                            .collect::<Vec<f32>>()
                    })
                    .unwrap_or_default()
            })
            .collect();
        if let Some(dim) = payload.get("dimension").and_then(serde_json::Value::as_u64)
            && let Ok(mut guard) = self.dimension.lock()
        {
            *guard = Some(dim as usize);
        }
        Ok(vectors)
    }
}

impl Embedder for SidecarEmbedder {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>> {
        if texts.is_empty() {
            return Ok(Vec::new());
        }
        let plane = self.plane;
        let mut guard = self
            .worker
            .lock()
            .map_err(|_| EmbedError::new("embed worker lock poisoned"))?;
        if guard.is_none() {
            *guard = Some(spawn_worker()?);
        }
        let request = self.request(texts);
        let mut respawned = false;
        let line = loop {
            {
                let worker = guard
                    .as_mut()
                    .ok_or_else(|| EmbedError::new("embed worker gone"))?;
                if writeln!(worker.stdin, "{request}")
                    .and_then(|()| worker.stdin.flush())
                    .is_err()
                {
                    if respawned {
                        return Err(EmbedError::new(format!(
                            "embed worker died and did not restart (plane={plane:?})"
                        )));
                    }
                    respawned = true;
                    let _ = worker.child.kill();
                    let _ = worker.child.wait();
                    *guard = Some(spawn_worker()?);
                    continue;
                }
            }
            let worker = guard
                .as_mut()
                .ok_or_else(|| EmbedError::new("embed worker gone"))?;
            match worker.responses.recv_timeout(REQUEST_TIMEOUT) {
                Ok(candidate) if candidate.starts_with('{') => break candidate,
                Ok(_) => continue, // startup noise on stdout
                Err(_) => {
                    if respawned {
                        return Err(EmbedError::new("embed worker timed out"));
                    }
                    respawned = true;
                    let _ = worker.child.kill();
                    let _ = worker.child.wait();
                    *guard = Some(spawn_worker()?);
                }
            }
        };
        Self::parse_response(self, &line)
    }

    fn dimension(&self) -> Option<usize> {
        *self.dimension.lock().ok()?
    }

    fn backend_name(&self) -> &'static str {
        "python"
    }
}

fn spawn_worker() -> Result<Worker> {
    let script = worker_script()?;
    let mut child = Command::new(python_binary())
        .arg(&script)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| {
            EmbedError::new(format!(
                "spawn embed worker ({}): {error}",
                script.display()
            ))
        })?;
    let stdin = child.stdin.take().ok_or_else(|| {
        EmbedError::new("embed worker stdin unavailable")
    })?;
    let stdout: ChildStdout = child
        .stdout
        .take()
        .ok_or_else(|| EmbedError::new("embed worker stdout unavailable"))?;
    let (sender, responses) = mpsc::channel::<String>();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            match line {
                Ok(line) => {
                    if sender.send(line).is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    });
    Ok(Worker {
        stdin,
        child,
        responses,
    })
}

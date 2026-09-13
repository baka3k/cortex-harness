//! bge-m3 dense-head embeddings — Plan B of phase 14: invoke the Python
//! embedding sidecar (repo `.venv` + cached `BAAI/bge-m3`) as a subprocess.
//! The ONNX `ort` port is a later spike and is intentionally NOT implemented
//! here.
//!
//! Model/device resolution mirrors `doc-tiny/embedding_utils.py`:
//! `EMBEDDING_MODEL_PATH`/`EMBEDDING_MODEL` env fallbacks and
//! `EMBEDDING_DEVICE` (default `cpu`).

use std::process::Command;

use serde_json::Value;

use crate::providers::{ProviderError, ProviderResult, python_binary};

const SIDECAR: &str = r#"
import json, os, sys
from pathlib import Path
req = json.loads(sys.stdin.read())
selected = req.get("model") or os.getenv("EMBEDDING_MODEL_PATH") or os.getenv("EMBEDDING_MODEL") or "BAAI/bge-m3"
local_files_only = Path(selected).exists()
if local_files_only or os.getenv("HF_HUB_OFFLINE") or os.getenv("TRANSFORMERS_OFFLINE"):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
device = req.get("device") or os.getenv("EMBEDDING_DEVICE") or "cpu"
from sentence_transformers import SentenceTransformer
model = SentenceTransformer(selected, local_files_only=local_files_only, device=device)
vectors = model.encode(req["texts"])
print(json.dumps({"dimension": int(model.get_sentence_embedding_dimension()), "vectors": vectors.tolist()}))
"#;

/// Encode texts through the Python sidecar. Returns `(dimension, vectors)`.
pub fn encode(
    texts: &[String],
    model: Option<&str>,
    device: Option<&str>,
) -> ProviderResult<(usize, Vec<Vec<f64>>)> {
    if texts.is_empty() {
        return Ok((0, Vec::new()));
    }
    let request = serde_json::json!({
        "texts": texts,
        "model": model,
        "device": device,
    });
    let output = Command::new(python_binary())
        .arg("-c")
        .arg(SIDECAR)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .and_then(|mut child| {
            use std::io::Write;
            child
                .stdin
                .as_mut()
                .expect("piped stdin")
                .write_all(request.to_string().as_bytes())?;
            let out = child.wait_with_output()?;
            if out.status.success() {
                Ok(out.stdout)
            } else {
                Err(std::io::Error::other(
                    String::from_utf8_lossy(&out.stderr).into_owned(),
                ))
            }
        })
        .map_err(|e| ProviderError(format!("embedding sidecar failed: {e}")))?;
    let payload: Value = serde_json::from_slice(&output)
        .map_err(|e| ProviderError(format!("embedding sidecar output: {e}")))?;
    let dimension = payload
        .get("dimension")
        .and_then(Value::as_u64)
        .unwrap_or(0) as usize;
    let vectors = payload
        .get("vectors")
        .and_then(Value::as_array)
        .map(|rows| {
            rows.iter()
                .map(|row| {
                    row.as_array()
                        .map(|nums| nums.iter().filter_map(Value::as_f64).collect())
                        .unwrap_or_default()
                })
                .collect()
        })
        .unwrap_or_default();
    Ok((dimension, vectors))
}

/// Cosine similarity helper used by the smoke gate.
pub fn cosine(a: &[f64], b: &[f64]) -> f64 {
    let dot: f64 = a.iter().zip(b).map(|(x, y)| x * y).sum();
    let norm_a = a.iter().map(|x| x * x).sum::<f64>().sqrt();
    let norm_b = b.iter().map(|x| x * x).sum::<f64>().sqrt();
    if norm_a == 0.0 || norm_b == 0.0 {
        return 0.0;
    }
    dot / (norm_a * norm_b)
}

#[cfg(test)]
mod sidecar {
    use super::*;

    #[test]
    #[ignore = "requires the local bge-m3 HF cache; run via: cargo test -p cortex-doc -- --ignored"]
    fn sidecar_matches_dimension() {
        let (dimension, vectors) = encode(
            &["Digital Key 3.0".to_string(), "FalkorDB graph".to_string()],
            None,
            None,
        )
        .unwrap();
        assert_eq!(dimension, 1024); // bge-m3 dense head
        assert_eq!(vectors.len(), 2);
        assert_eq!(vectors[0].len(), 1024);
        // Same-text similarity must beat cross-text similarity.
        let (_, again) = encode(&["Digital Key 3.0".to_string()], None, None).unwrap();
        let self_sim = cosine(&vectors[0], &again[0]);
        let cross_sim = cosine(&vectors[0], &vectors[1]);
        assert!(self_sim > cross_sim, "self={self_sim} cross={cross_sim}");
        assert!(self_sim > 0.999, "self-similarity drifted: {self_sim}");
    }
}

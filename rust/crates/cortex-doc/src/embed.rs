//! bge-m3 dense-head embeddings cho lane doc/ingest.
//!
//! Hai backend đứng sau `encode()`, chọn bằng `CORTEX_EMBED_BACKEND`
//! (plans/260914-1706-onnx-embedding-spike phase-02):
//!
//! - `python` (mặc định): one-shot sidecar — `torch`/sentence-transformers không
//!   bao giờ vào Rust runtime. Đây là hành vi plan B của phase 14, giữ nguyên để
//!   rollback tức thì.
//! - `onnx`: `cortex_embed::OnnxEmbedder` (ort, CPU).
//!
//! Số học doc plane đã đối chiếu bằng chứng (`findings.md` C2): bge-m3 là
//! **CLS-pool + L2-normalize** (`modules.json` có `2_Normalize`), max 8192 token,
//! fp32 — không phải "encode raw không normalize" như bản plan cũ.
//!
//! Model/device resolution mirror `doc-tiny/embedding_utils.py`:
//! `EMBEDDING_MODEL_PATH`/`EMBEDDING_MODEL` + `EMBEDDING_DEVICE` (default `cpu`).

use std::process::Command;

use cortex_embed::{Backend, Embedder, OnnxEmbedder, Plane, spec_from_env, spec_from_source};
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

/// Giá trị cho key `python` trong output `cortex-doc embed`: parity harness
/// (`doctiny_parity.py`) in nó ra để cho biết số liệu đến từ backend nào.
#[must_use]
pub fn backend_label() -> String {
    match Backend::from_env() {
        Backend::Onnx => String::from("cortex-embed ort (onnx)"),
        Backend::Python => python_binary(),
    }
}

/// Encode texts. Returns `(dimension, vectors)`.
pub fn encode(
    texts: &[String],
    model: Option<&str>,
    device: Option<&str>,
) -> ProviderResult<(usize, Vec<Vec<f64>>)> {
    if texts.is_empty() {
        return Ok((0, Vec::new()));
    }
    match Backend::from_env() {
        Backend::Onnx => encode_onnx(texts, model),
        Backend::Python => encode_sidecar(texts, model, device),
    }
}

/// `cortex-embed` native: cùng graph, cùng số học, không spawn Python.
fn encode_onnx(texts: &[String], model: Option<&str>) -> ProviderResult<(usize, Vec<Vec<f64>>)> {
    if let Some(note) = Backend::device_note(Plane::Doc) {
        eprintln!("[cortex-doc] {note}");
    }
    let requested = model.map(str::trim).filter(|value| !value.is_empty());
    let spec = match requested {
        Some(name) => spec_from_source(Plane::Doc, name),
        None => spec_from_env(Plane::Doc),
    }
    .map_err(|error| ProviderError(format!("embed backend resolve: {error}")))?;
    let embedder = OnnxEmbedder::new(spec)
        .map_err(|error| ProviderError(format!("onnx embedder: {error}")))?;
    let vectors = embedder
        .embed(texts)
        .map_err(|error| ProviderError(format!("onnx embed: {error}")))?;
    let dimension = embedder.dimension().ok_or_else(|| {
        ProviderError("onnx backend reported no embedding dimension".to_string())
    })?;
    Ok((
        dimension,
        vectors
            .into_iter()
            .map(|vector| vector.into_iter().map(f64::from).collect())
            .collect(),
    ))
}

/// One-shot Python sidecar (hành vi kế thừa từ phase 14 plan B).
fn encode_sidecar(
    texts: &[String],
    model: Option<&str>,
    device: Option<&str>,
) -> ProviderResult<(usize, Vec<Vec<f64>>)> {
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

    /// Gate phase-02: cùng text, hai backend phải cho cosine >= 0.999.
    /// Gọi thẳng hai hàm (không set env) để không cần `unsafe` và không race
    /// với test song song.
    #[test]
    #[ignore = "requires bge-m3 ONNX artifacts + .venv sidecar; run `make embed-artifacts`"]
    fn onnx_backend_matches_python_backend() {
        let texts: Vec<String> = [
            "Digital Key 3.0 defines a secure element profile for car access.",
            "FalkorDB graph provider stores paragraphs and entity mentions.",
            "Bảng kê công nợ phải khớp trước khi chốt kỳ kế toán.",
        ]
        .iter()
        .map(|value| (*value).to_string())
        .collect();

        let (python_dim, python) = encode_sidecar(&texts, None, None).expect("python backend");
        let (onnx_dim, onnx) = encode_onnx(&texts, None).expect("onnx backend");

        assert_eq!(python_dim, onnx_dim);
        assert_eq!(python_dim, 1024);
        for index in 0..texts.len() {
            let similarity = cosine(&python[index], &onnx[index]);
            assert!(
                similarity >= 0.999,
                "case {index} ({}): python vs onnx cosine {similarity}",
                texts[index]
            );
        }
    }
}

//! Seam duy nhất mà mọi caller (mind MCP, cortex-doc, query lanes) chạm vào,
//! cộng với selection qua env `CORTEX_EMBED_BACKEND=python|onnx` (default
//! `python` — rollback tức thì, cùng pattern `CORTEX_RUST_ANALYZER`).

use crate::error::Result;
use crate::model::{resolve_model_source, spec_from_env, Plane};
use crate::onnx::OnnxEmbedder;
use crate::sidecar::SidecarEmbedder;

/// Một batch text -> vectors. `dimension()` trả `None` cho tới khi backend biết
/// chiều thật: sidecar Python chỉ echo `dimension` sau response đầu tiên, còn
/// ONNX thì có ngay từ spec.
pub trait Embedder: Send + Sync {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>>;

    fn dimension(&self) -> Option<usize>;

    /// `onnx` | `python` — ghi vào report parity để biết số nào từ đâu.
    fn backend_name(&self) -> &'static str;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Backend {
    Python,
    Onnx,
}

pub const BACKEND_ENV: &str = "CORTEX_EMBED_BACKEND";

impl Backend {
    /// Default `python`: gate parity/perf chưa pass cho tới khi nói khác đi.
    pub fn from_env() -> Self {
        match read_env(BACKEND_ENV).as_deref() {
            Some("onnx") => Self::Onnx,
            Some("python") => Self::Python,
            Some(other) => {
                eprintln!(
                    "[cortex-embed] unknown {BACKEND_ENV}={other:?}; falling back to python"
                );
                Self::Python
            }
            None => Self::Python,
        }
    }

    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Python => "python",
            Self::Onnx => "onnx",
        }
    }

    /// spike này chỉ chạy CPU (findings C5: golden vectors phải generate trên CPU
    /// để loại drift non-determinism). `EMBED_DEVICE`/`EMBEDDING_DEVICE` khác cpu
    /// -> warn + vẫn cpu, đúng ý phase-01.
    #[must_use]
    pub fn device_note(plane: Plane) -> Option<String> {
        let key = match plane {
            Plane::Code => "EMBED_DEVICE",
            Plane::Doc => "EMBEDDING_DEVICE",
        };
        match read_env(key).as_deref() {
            None | Some("cpu") | Some("auto") => None,
            Some(requested) => Some(format!(
                "{key}={requested:?} ignored: cortex-embed is CPU-only in this spike"
            )),
        }
    }
}

pub fn read_env(key: &str) -> Option<String> {
    std::env::var(key)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

/// Embedder theo env hiện tại cho một plane. Không hardcode model: chuỗi env của
/// plane đó quyết định model, để Rust và Python luôn so trên cùng artifact.
pub fn embedder_for(plane: Plane) -> Result<Box<dyn Embedder>> {
    let backend = Backend::from_env();
    if let Some(note) = Backend::device_note(plane) {
        eprintln!("[cortex-embed] {note}");
    }
    match backend {
        Backend::Onnx => {
            let spec = spec_from_env(plane)?;
            Ok(Box::new(OnnxEmbedder::new(spec)?))
        }
        Backend::Python => Ok(Box::new(SidecarEmbedder::new(
            plane,
            resolve_model_source(plane, &|key| std::env::var(key).ok()),
        ))),
    }
}

/// Tên model đang dùng, phục vụ `dev doctor` / log.
#[must_use]
pub fn model_for(plane: Plane) -> String {
    resolve_model_source(plane, &|key| std::env::var(key).ok())
}

//! # cortex-embed
//!
//! Rust native embedder (ONNX Runtime qua crate `ort` + `tokenizers`) cho spike
//! `plans/260914-1706-onnx-embedding-spike`: bỏ Python sidecar khỏi đường
//! embedding, gate parity **cosine >= 0.999** so với Python.
//!
//! Hai backend đứng sau một trait (`Embedder`), chọn bằng
//! `CORTEX_EMBED_BACKEND=python|onnx` (default `python` = rollback tức thì):
//!
//! - [`onnx::OnnxEmbedder`] — graph ONNX fp32 chạy CPU bằng `ort` (load-dynamic,
//!   không download ORT lúc build).
//! - [`sidecar::SidecarEmbedder`] — worker Python persistent hiện tại, giữ để A/B.
//!
//! Số học đã đối chiếu bằng chứng và ghi trong `plans/260914-1706-onnx-embedding-spike/findings.md`:
//! jina-v3 = mean-pool fp32 + L2-normalize, LoRA tắt, token max 8194; bge-m3 =
//! CLS-pool + L2-normalize, token max 8192. `mean-pool/không-normalize/512` chỉ còn
//! là mode generic cho model thay thế qua `CODE_EMBEDDING_MODEL_PATH`.

mod backend;
mod error;
mod model;
mod onnx;
mod pooling;
mod sidecar;

pub use backend::{
    Backend, Embedder, BACKEND_ENV, embedder_for, model_for, read_env, trace_enabled,
};
pub use error::{EmbedError, Result};
pub use model::{
    BGE_MAX_TOKENS, CODE_MAX_CHARS, EMBEDDING_DIMENSION, JINA_MAX_TOKENS, ModelPin, ModelSpec,
    Plane, Pooling, export_dir, hf_snapshot, hub_dir, is_python_space, model_pin, python_strip,
    file_sha256, repo_root, resolve_model_source, spec_from_env, spec_from_source, metadata_exported_graph,
};
pub use onnx::{OnnxEmbedder, SessionConfig, ort_dylib};
pub use pooling::{cls_pool, l2_normalize, mean_of_chunks, mean_pool};
pub use sidecar::SidecarEmbedder;

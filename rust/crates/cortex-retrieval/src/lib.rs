//! Cortex retrieval brain — Rust port của `code-tiny/tools/common`.
//!
//! Parity contract: mỗi module phải pass golden fixtures sinh từ implementation
//! Python (`scripts/rust_parity/`), tolerance 1e-9 cho scores.

pub mod bm25;
pub mod fusion;
pub mod intent;
pub mod query_understanding;
pub mod signal_normalize;

//! Cortex graph writer — Rust port của `code-tiny/tools/graph` writer plane
//! (phase 03 của `260913-2130-rust-full-migration`).
//!
//! Ported surface:
//! - `writer/query_contract.py`  → [`query_contract`]  (MERGE upsert compiler,
//!   endpoint audit — byte-exact Cypher)
//! - `writer/language_writer.py` → [`language_writer`] (batch write contract,
//!   node/rel upsert pipelines, evidence edges)
//! - `operations/*`              → [`operations`]     (9 op modules)
//! - `writer/project_topology_writer.py` + `project_topology/models.py`
//!   (writer half)               → [`topology`]
//! - `schema/preflight.py`       → [`preflight`]
//! - `graph/core/base.py` driver contract → [`store`] (trait [`store::GraphStore`]
//!   với 2 backend: FalkorDB remote + LadybugDB embedded, provider selection
//!   theo env như `GraphDriverFactory`)
//!
//! Parity contract: các query template giữ nguyên từng chữ so với Python; dual-write
//! diff (Python writer ↔ Rust writer trên 2 graph cùng backend) là gate của phase.
//! Ghi chú dialect ladybug trong [`store::ladybug_store`].

pub mod json_row;
pub mod language_writer;
pub mod operations;
pub mod preflight;
pub mod project_scope;
pub mod query_contract;
pub mod query_normalize;
pub mod store;
pub mod topology;
pub mod upserts;

pub use language_writer::LanguageCodeWriter;
pub use store::{GraphStore, StoreError, open_store_from_env};
pub use topology::{ProjectTopologyWriter, stable_fact_id};

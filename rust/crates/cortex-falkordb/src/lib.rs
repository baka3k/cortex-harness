//! cortex-falkordb — FalkorDB/Redis graph client cho CortexHarness.
//!
//! Phase 01 của plans/260913-2130-rust-full-migration: spike go/no-go cho graph
//! path Rust (redis-rs + GRAPH.RO_QUERY/GRAPH.QUERY compact protocol). Client
//! tái hiện wire contract của falkordb-py mà `falkordb_driver.py` đang dùng.
//!
//! - [`client::FalkorDbClient`]: kết nối RESP2, ro_query/query với params.
//! - [`value::FalkorValue`]: parser result set compact (Node/Edge/Path/...).
//! - [`normalize::normalize_value`]: JSON khớp `_normalize_falkordb_value`.

pub mod client;
pub mod normalize;
pub mod schema;
pub mod value;

pub use client::{ClientError, Column, FalkorDbClient, Param, QueryResult};
pub use schema::GraphSchema;
pub use value::FalkorValue;

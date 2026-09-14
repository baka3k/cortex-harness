//! Rust port của CortexHarness MCP server framework — phase 11 của
//! `plans/260913-2130-rust-full-migration`.
//!
//! Ported modules:
//! * `cortex_harness/mcp_contract.py`  → [`contract`]
//!   (wire envelope `cortex.mcp.tool-result` v1.0, byte-compatible)
//! * `code-tiny/mcp/tool_metadata.py`  → [`catalog`]
//!   (full tool catalog + `build_catalog`)
//! * `code-tiny/mcp/framework_registry.py` → [`framework_registry`]
//!   (canonical parser capability matrix)
//! * `code-tiny/tools/common/project_registry.py` → [`project_registry`]
//!   (project_id → storage targets; reuses
//!   `cortex_graph_writer::project_scope` for `project_id_lookup_key` /
//!   `prepare_project_scope_parameters`)
//! * `code-tiny/mcp/unified_mcp.py` (dispatch pre-flight + middleware
//!   envelope wrapping) → [`dispatch`]
//! * planner tools `compute_scc` / `topological_sort`
//!   (from `code-tiny/mcp/fastmcp_server.py`) → [`planner`]
//! * rmcp streamable-HTTP server (replaces `fastmcp` transport on
//!   `:8788` / `:8789`, path `/mcp`) → [`server`]
//!
//! Phase-11 boundary: tools that need the graph run the exact Python
//! pre-flight (defaults → list coercion → missing-required → parser →
//! project resolution) and then return the canonical
//! `capability_unavailable` envelope; real graph tools land in phase 12.

pub mod catalog;
pub mod contract;
pub mod dispatch;
pub mod framework_registry;
pub mod graph;
pub mod planner;
pub mod project_registry;
pub mod server;

pub use contract::{
    normalize_error, normalize_success, result_meta, result_summary, MCP_CONTRACT_NAME,
    MCP_CONTRACT_VERSION,
};

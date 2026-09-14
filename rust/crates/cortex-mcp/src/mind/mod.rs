//! Phase-13 mind tools — port of the `doc-tiny/mcp_graph_rag.py` MCP server
//! (`mind_mcp`) on top of the shared phase-11 contract layer.
//!
//! The Python deployment runs TWO servers: the unified `graph_mcp`
//! (`code-tiny/mcp/unified_mcp.py`, phase 11/12 surface) and the doc
//! `mind_mcp` (`doc-tiny/mcp_graph_rag.py`, this surface). The Rust binary
//! mirrors that split with a server *flavor* (`--server mind` /
//! `MCP_SERVER_NAME=mind_mcp`): same process, mind tool catalog + mind
//! dispatch instead of the unified one.
//!
//! Python-plane notes:
//! * query embedding — Python sidecar (Plan B, [`embed`]);
//! * GLiNER extraction — Python sidecar at *ingest* time only; the mind
//!   tools read entities from the FalkorDB store (no query-time NER);
//! * local-mode Qdrant — collection listing via directory scan, vector
//!   search is Python-plane ([`qdrant`]).

pub mod catalog;
pub mod embed;
pub mod graphstore;
pub mod qdrant;
pub mod tools;

use crate::dispatch::{ToolOutcome, is_missing_value};
use serde_json::Value;

/// FastMCP signature validation surface for the mind flavor: which params a
/// transport-level missing-argument error names, in signature order.
fn signature_required(tool_name: &str) -> &'static [&'static str] {
    match tool_name {
        "semantic_search" | "query_graph_rag_langextract" => &["query"],
        "get_paragraph_text" => &["source_id", "paragraph_id"],
        _ => &[],
    }
}

/// FastMCP-level missing-argument guard. The doc server's `mcp` lib renders
/// pydantic validation errors as
/// `Error executing tool <name>: N validation error(s) for <Name>Arguments`
/// (byte-matched against the recorded live fixtures).
fn signature_guard(tool_name: &str, arguments: &Value) -> Option<ToolOutcome> {
    let required = signature_required(tool_name);
    let missing: Vec<&str> = required
        .iter()
        .copied()
        .filter(|parameter| {
            arguments
                .get(*parameter)
                .map(is_missing_value)
                .unwrap_or(true)
        })
        .collect();
    if missing.is_empty() {
        return None;
    }
    let arguments_name = format!("{tool_name}Arguments");
    let section = |parameter: &str| {
        format!(
            "{parameter}\n  Field required [type=missing, input_value={{}}, \
             input_type=dict]\n    For further information visit \
             https://errors.pydantic.dev/2.13/v/missing"
        )
    };
    let sections: Vec<String> = missing.iter().map(|parameter| section(parameter)).collect();
    let plural = if missing.len() == 1 { "error" } else { "errors" };
    let content_text = format!(
        "Error executing tool {tool_name}: {count} validation {plural} for \
         {arguments_name}\n{body}",
        count = missing.len(),
        body = sections.join("\n"),
    );
    Some(ToolOutcome::RawError { content_text })
}

/// The mind dispatch outcome for one tool call (mirrors `_standard_tool`'s
/// contract: tool body result → `wrap_dispatch_result`, exception →
/// `normalize_error` envelope).
pub fn dispatch_mind_tool(tool_name: &str, arguments: &Value) -> ToolOutcome {
    if let Some(outcome) = signature_guard(tool_name, arguments) {
        return outcome;
    }
    let result = match tool_name {
        "list_source_ids" => tools::tool_list_source_ids(arguments),
        "list_qdrant_collections" => tools::tool_list_qdrant_collections(arguments),
        "semantic_search" => tools::tool_semantic_search(arguments),
        "query_graph_rag_langextract" => tools::tool_query_graph_rag(arguments),
        "get_paragraph_text" => tools::tool_get_paragraph_text(arguments),
        other => {
            return ToolOutcome::RawError {
                content_text: format!("Tool not found: {other}"),
            };
        }
    };
    match result {
        Ok(payload) => ToolOutcome::Payload(payload),
        Err(error) => {
            // `_standard_tool` exception path → canonical error envelope.
            let envelope = crate::contract::normalize_error(
                crate::contract::ErrorValue::Exception {
                    name: error.exception,
                    message: Some(&error.message),
                },
                None,
                None,
                None,
                None,
            );
            let message = envelope["error"]["message"]
                .as_str()
                .unwrap_or("Tool execution failed.")
                .to_string();
            ToolOutcome::EnvelopeError { envelope, content_text: message }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn unknown_tool_is_raw_error() {
        let outcome = dispatch_mind_tool("bogus_tool", &json!({}));
        match outcome {
            ToolOutcome::RawError { content_text } => {
                assert_eq!(content_text, "Tool not found: bogus_tool");
            }
            other => panic!("unexpected outcome: {other:?}"),
        }
    }

    #[test]
    fn signature_guard_names_missing_required() {
        let outcome = dispatch_mind_tool("semantic_search", &json!({}));
        match outcome {
            ToolOutcome::RawError { content_text } => {
                assert!(
                    content_text.starts_with(
                        "Error executing tool semantic_search: 1 validation error \
                         for semantic_searchArguments\nquery\n  Field required"
                    ),
                    "{content_text}"
                );
            }
            other => panic!("unexpected outcome: {other:?}"),
        }
        let outcome = dispatch_mind_tool("get_paragraph_text", &json!({}));
        match outcome {
            ToolOutcome::RawError { content_text } => {
                assert!(
                    content_text.contains("2 validation errors for get_paragraph_textArguments"),
                    "{content_text}"
                );
                assert!(content_text.contains("source_id\n  Field required"));
                assert!(content_text.contains("paragraph_id\n  Field required"));
            }
            other => panic!("unexpected outcome: {other:?}"),
        }
    }
}

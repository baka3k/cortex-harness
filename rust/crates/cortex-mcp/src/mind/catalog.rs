//! Mind tool metadata — mirrors the FastMCP registrations in
//! `doc-tiny/mcp_graph_rag.py::register_tools` (names + docstring
//! descriptions + signature parameters), so `tools/list` on the mind flavor
//! matches the Python `mind_mcp` server.
//!
//! Descriptions are byte-copies of the Python docstrings; parameter names
//! follow the Python signatures (`inspect.signature` → FastMCP schema).

use serde_json::{json, Map, Value};

/// One mind tool parameter: Python-flavoured name + JSON-schema type.
struct Param {
    name: &'static str,
    /// JSON-schema kind tag rendered at runtime.
    kind: ParamKind,
    required: bool,
}

#[derive(Clone, Copy)]
enum ParamKind {
    Str,
    Int,
    Bool,
    Number,
    OptionalStr,
    OptionalInt,
    OptionalNumber,
    OptionalStringList,
}

fn optional(schema: Value) -> Value {
    json!({ "anyOf": [schema, { "type": "null" }] })
}

impl ParamKind {
    fn schema(self) -> Value {
        match self {
            Self::Str => json!({ "type": "string" }),
            Self::Int => json!({ "type": "integer" }),
            Self::Bool => json!({ "type": "boolean" }),
            Self::Number => json!({ "type": "number" }),
            Self::OptionalStr => optional(json!({ "type": "string" })),
            Self::OptionalInt => optional(json!({ "type": "integer" })),
            Self::OptionalNumber => optional(json!({ "type": "number" })),
            Self::OptionalStringList => json!({
                "anyOf": [
                    { "type": "array", "items": { "type": "string" } },
                    { "type": "string" },
                    { "type": "null" },
                ]
            }),
        }
    }
}

struct MindTool {
    name: &'static str,
    /// Byte-copy of the Python tool docstring.
    description: &'static str,
    params: &'static [Param],
}

const LIST_SOURCE_IDS_PARAMS: &[Param] = &[
    Param { name: "limit", kind: ParamKind::Int, required: false },
    Param { name: "project_id", kind: ParamKind::OptionalStr, required: false },
];

const LIST_QDRANT_COLLECTIONS_PARAMS: &[Param] = &[Param {
    name: "project_id",
    kind: ParamKind::OptionalStr,
    required: false,
}];

const SEMANTIC_SEARCH_PARAMS: &[Param] = &[
    Param { name: "query", kind: ParamKind::Str, required: true },
    Param { name: "top_k", kind: ParamKind::Int, required: false },
    Param { name: "source_id", kind: ParamKind::OptionalStr, required: false },
    Param { name: "collection", kind: ParamKind::OptionalStr, required: false },
    Param { name: "project_id", kind: ParamKind::OptionalStr, required: false },
    Param { name: "max_passage_chars", kind: ParamKind::OptionalInt, required: false },
    Param { name: "include_entity_ids", kind: ParamKind::Bool, required: false },
    Param { name: "include_entity_mentions", kind: ParamKind::Bool, required: false },
];

const QUERY_GRAPH_RAG_PARAMS: &[Param] = &[
    Param { name: "query", kind: ParamKind::Str, required: true },
    Param { name: "top_k", kind: ParamKind::Int, required: false },
    Param { name: "source_id", kind: ParamKind::OptionalStr, required: false },
    Param { name: "collection", kind: ParamKind::OptionalStr, required: false },
    Param { name: "include_entities", kind: ParamKind::Bool, required: false },
    Param { name: "include_relations", kind: ParamKind::Bool, required: false },
    Param { name: "expand_related", kind: ParamKind::Bool, required: false },
    Param { name: "related_k", kind: ParamKind::Int, required: false },
    Param { name: "graph_depth", kind: ParamKind::Int, required: false },
    Param { name: "entity_types", kind: ParamKind::OptionalStringList, required: false },
    Param { name: "max_passage_chars", kind: ParamKind::OptionalInt, required: false },
    Param { name: "min_score_to_expand", kind: ParamKind::OptionalNumber, required: false },
    Param { name: "min_entity_occurrences", kind: ParamKind::OptionalInt, required: false },
    Param { name: "rerank", kind: ParamKind::Bool, required: false },
    Param { name: "rerank_entity_weight", kind: ParamKind::Number, required: false },
    Param { name: "rerank_type_weight", kind: ParamKind::Number, required: false },
    Param { name: "rerank_confidence_weight", kind: ParamKind::Number, required: false },
    Param { name: "rerank_length_penalty", kind: ParamKind::Number, required: false },
    Param { name: "project_id", kind: ParamKind::OptionalStr, required: false },
];

const GET_PARAGRAPH_TEXT_PARAMS: &[Param] = &[
    Param { name: "source_id", kind: ParamKind::Str, required: true },
    Param { name: "paragraph_id", kind: ParamKind::Int, required: true },
    Param { name: "project_id", kind: ParamKind::OptionalStr, required: false },
];

const LIST_SOURCE_IDS_DESC: &str = "List available source_id values from Neo4j (Paragraph nodes).";
const LIST_QDRANT_COLLECTIONS_DESC: &str = "List Qdrant collections.\n\n        Per the unified ingest/query contract:\n        - ``project_id`` is optional. When omitted (or empty), returns every\n          collection (``None`` semantics = full-search across all projects).\n        - When supplied AND registered, filters to that project's collection.\n        - When supplied but not registered, fails closed with the project\n          registry error instead of silently querying another project's data.\n        ";
const SEMANTIC_SEARCH_DESC: &str = "Vector-only search in Qdrant. Returns passages without graph expansion.\n\n        Per Phase 05 of the unified ingest/query contract plan, ``project_id``\n        scopes the query to one project's shard; omit it to search across all\n        projects. The Qdrant collection is resolved through the registry when\n        ``project_id`` is given; the explicit ``collection`` arg still wins as\n        an escape hatch.\n        ";
const QUERY_GRAPH_RAG_DESC: &str = "\n        Query Qdrant for top-k passages with entity_ids payload, then fetch related\n        entity context from Neo4j. Returns context only (no LLM generation).\n        ";
const GET_PARAGRAPH_TEXT_DESC: &str =
    "Fetch a paragraph's text by source_id + paragraph_id from Neo4j.";

const MIND_TOOLS: &[MindTool] = &[
    MindTool {
        name: "list_source_ids",
        description: LIST_SOURCE_IDS_DESC,
        params: LIST_SOURCE_IDS_PARAMS,
    },
    MindTool {
        name: "list_qdrant_collections",
        description: LIST_QDRANT_COLLECTIONS_DESC,
        params: LIST_QDRANT_COLLECTIONS_PARAMS,
    },
    MindTool {
        name: "semantic_search",
        description: SEMANTIC_SEARCH_DESC,
        params: SEMANTIC_SEARCH_PARAMS,
    },
    MindTool {
        name: "query_graph_rag_langextract",
        description: QUERY_GRAPH_RAG_DESC,
        params: QUERY_GRAPH_RAG_PARAMS,
    },
    MindTool {
        name: "get_paragraph_text",
        description: GET_PARAGRAPH_TEXT_DESC,
        params: GET_PARAGRAPH_TEXT_PARAMS,
    },
];

/// Mind tool names in registration order.
pub fn mind_tool_names() -> Vec<&'static str> {
    MIND_TOOLS.iter().map(|tool| tool.name).collect()
}

pub fn is_mind_tool(name: &str) -> bool {
    MIND_TOOLS.iter().any(|tool| tool.name == name)
}

/// Required signature parameters (FastMCP validates these at the transport
/// boundary before the tool body runs).
pub fn required_params(name: &str) -> Vec<&'static str> {
    MIND_TOOLS
        .iter()
        .find(|tool| tool.name == name)
        .map(|tool| {
            tool.params
                .iter()
                .filter(|param| param.required)
                .map(|param| param.name)
                .collect()
        })
        .unwrap_or_default()
}

/// One `rmcp::model::Tool`-shaped JSON entry: name, description, schema.
pub fn mind_tool_entries() -> Vec<(String, String, Map<String, Value>)> {
    MIND_TOOLS
        .iter()
        .map(|tool| {
            let mut properties = Map::new();
            let mut required: Vec<Value> = Vec::new();
            for param in tool.params {
                properties.insert(param.name.to_string(), param.kind.schema());
                if param.required {
                    required.push(json!(param.name));
                }
            }
            let mut schema = Map::new();
            schema.insert("type".to_string(), json!("object"));
            schema.insert("properties".to_string(), Value::Object(properties));
            if !required.is_empty() {
                schema.insert("required".to_string(), Value::Array(required));
            }
            (
                tool.name.to_string(),
                tool.description.to_string(),
                schema,
            )
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mind_catalog_covers_five_tools() {
        assert_eq!(
            mind_tool_names(),
            vec![
                "list_source_ids",
                "list_qdrant_collections",
                "semantic_search",
                "query_graph_rag_langextract",
                "get_paragraph_text",
            ]
        );
        assert!(is_mind_tool("semantic_search"));
        assert!(!is_mind_tool("search_functions"));
    }

    #[test]
    fn required_params_match_signatures() {
        assert_eq!(required_params("semantic_search"), vec!["query"]);
        assert_eq!(
            required_params("get_paragraph_text"),
            vec!["source_id", "paragraph_id"]
        );
        assert!(required_params("list_source_ids").is_empty());
    }

    #[test]
    fn descriptions_are_byte_copies() {
        let entries = mind_tool_entries();
        let semantic = entries
            .iter()
            .find(|(name, _, _)| name == "semantic_search")
            .unwrap();
        assert_eq!(
            semantic.1,
            "Vector-only search in Qdrant. Returns passages without graph expansion.\n\n        \
             Per Phase 05 of the unified ingest/query contract plan, ``project_id``\n        \
             scopes the query to one project's shard; omit it to search across all\n        \
             projects. The Qdrant collection is resolved through the registry when\n        \
             ``project_id`` is given; the explicit ``collection`` arg still wins as\n        \
             an escape hatch.\n        "
        );
    }
}

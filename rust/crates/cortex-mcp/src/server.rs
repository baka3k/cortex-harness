//! rmcp server khung — thay `fastmcp` transport (streamable HTTP trên
//! `:8788`/`:8789`, path `/mcp`) của `fastmcp_server.py` + `unified_mcp.py`.
//!
//! Wire surface (phase 11):
//! * `initialize` → serverInfo `graph_mcp` / `1.2.0` + unified instructions.
//! * `tools/list` → 39 unified tools; descriptions byte-matched to the
//!   Python registration (catalog description for proxied tools, the
//!   `@mcp_server.tool` description for unified-registered tools).
//! * `tools/call` → the unified dispatch pre-flight, then the canonical
//!   `cortex.mcp.tool-result` v1.0 envelope in `structuredContent`, with
//!   `_meta = {contract, contractVersion, tool}` and bounded content text,
//!   byte-compatible with the Python `_ProxyMiddleware` wrapping.

use std::borrow::Cow;
use std::sync::Arc;

use rmcp::handler::server::ServerHandler;
use rmcp::model::{
    CallToolRequestParams, CallToolResponse, CallToolResult, ContentBlock, Implementation,
    ListToolsResult, PaginatedRequestParams, ProtocolVersion, ServerCapabilities, ServerInfo,
    Tool, ToolsCapability,
};
use rmcp::service::{RequestContext, RoleServer};
use serde_json::{json, Map, Value};

use crate::catalog;
use crate::dispatch;

/// Unified server instructions (byte-matched from `unified_mcp.py`).
fn instructions() -> &'static str {
    &catalog::unified().instructions
}

/// Tools served by the unified server (39), in canonical catalog order.
pub fn served_tools() -> Vec<Tool> {
    let names: std::collections::BTreeSet<String> =
        catalog::unified().tool_names.iter().cloned().collect();
    catalog::build_catalog(&names, None)
        .iter()
        .map(|entry| {
            let name = entry["name"].as_str().expect("tool name").to_string();
            let description = catalog::wire_description(&name);
            let mut schema = Map::new();
            schema.insert("type".to_string(), json!("object"));
            let mut properties = Map::new();
            let mut required: Vec<Value> = Vec::new();
            if let Some(inputs) = entry.get("inputs").and_then(Value::as_array) {
                for input in inputs {
                    let Some(input_object) = input.as_object() else {
                        continue;
                    };
                    let Some(parameter_name) = input_object.get("name").and_then(Value::as_str)
                    else {
                        continue;
                    };
                    let mut property = Map::new();
                    property.insert(
                        "type".to_string(),
                        json!(json_type_of(input_object.get("type").and_then(Value::as_str))),
                    );
                    if let Some(description) = input_object.get("description") {
                        property.insert("description".to_string(), description.clone());
                    }
                    properties.insert(parameter_name.to_string(), Value::Object(property));
                    if input_object.get("required").and_then(Value::as_bool) == Some(true) {
                        required.push(json!(parameter_name));
                    }
                }
            }
            schema.insert("properties".to_string(), Value::Object(properties));
            if !required.is_empty() {
                schema.insert("required".to_string(), Value::Array(required));
            }
            Tool::new_with_raw(
                name,
                description.map(Cow::Owned),
                Arc::new(schema),
            )
        })
        .collect()
}

/// Map the catalog's Python-flavoured type strings onto JSON-schema types
/// (informational; FastMCP derives live schemas from Python signatures).
fn json_type_of(catalog_type: Option<&str>) -> &'static str {
    match catalog_type {
        Some("str") => "string",
        Some("int") | Some("float") => "number",
        Some("bool") => "boolean",
        Some(kind) if kind.starts_with("List[") => "array",
        Some(kind) if kind.starts_with("Dict[") => "object",
        _ => "string",
    }
}

/// Server flavor — the Python deployment runs two servers: the unified
/// `graph_mcp` (`code-tiny/mcp/unified_mcp.py`) and the doc `mind_mcp`
/// (`doc-tiny/mcp_graph_rag.py`). The Rust binary mirrors the split.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ServerFlavor {
    /// Unified code-graph server (phases 11/12).
    #[default]
    Graph,
    /// Doc/mind server (`mcp_graph_rag.py`, phase 13).
    Mind,
}

impl ServerFlavor {
    /// Resolve from the `--server` value / `MCP_SERVER_NAME` env:
    /// `mind`|`mind_mcp` → [`ServerFlavor::Mind`], anything else → graph.
    pub fn resolve(value: Option<&str>) -> Self {
        let normalized = value
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_lowercase)
            .unwrap_or_default();
        if normalized == "mind" || normalized == "mind_mcp" {
            Self::Mind
        } else {
            Self::Graph
        }
    }

    fn server_info_name(self) -> &'static str {
        match self {
            Self::Graph => "graph_mcp",
            Self::Mind => "mind_mcp",
        }
    }
}

/// Tools served by the mind server (`mcp_graph_rag.py::register_tools`) —
/// name + description + schema entries.
pub fn mind_served_tools() -> Vec<Tool> {
    crate::mind::catalog::mind_tool_entries()
        .into_iter()
        .map(|(name, description, schema)| {
            Tool::new_with_raw(name, Some(Cow::Owned(description)), Arc::new(schema))
        })
        .collect()
}

/// One deterministic mind tool call, from arguments to the wire result
/// (mirrors `_standard_tool`: tool body → success envelope; exception →
/// canonical error envelope).
pub fn call_mind_tool_payload(tool_name: &str, arguments: &Value) -> CallToolResult {
    match crate::mind::dispatch_mind_tool(tool_name, arguments) {
        dispatch::ToolOutcome::RawError { content_text } => {
            let mut result = CallToolResult::error(vec![ContentBlock::text(content_text)]);
            result.result_type = None;
            result
        }
        dispatch::ToolOutcome::EnvelopeError { envelope, content_text } => {
            let mut result = CallToolResult::error(vec![ContentBlock::text(content_text)]);
            result.structured_content = Some(envelope);
            result.meta = Some(rmcp::model::MetaObject(crate::contract::result_meta(Some(tool_name))));
            result.result_type = None;
            result
        }
        dispatch::ToolOutcome::Payload(payload) => {
            let wire = dispatch::wrap_dispatch_result(&payload, tool_name);
            if wire.is_error {
                let mut result = CallToolResult::error(vec![ContentBlock::text(wire.content_text)]);
                result.structured_content = wire.structured_content;
                result.meta = Some(rmcp::model::MetaObject(wire.meta));
                result.result_type = None;
                return result;
            }
            let mut result =
                CallToolResult::structured(wire.structured_content.clone().unwrap_or(Value::Null));
            result.content = vec![ContentBlock::text(wire.content_text)];
            result.meta = Some(rmcp::model::MetaObject(wire.meta));
            result.result_type = None;
            result
        }
    }
}

/// `CortexMcpServer` — stateless handler (the streamable service factory
/// creates one per request, no session state, mirroring
/// `stateless_http=True` in `unified_mcp.py`).
#[derive(Debug, Clone, Default)]
pub struct CortexMcpServer {
    flavor: ServerFlavor,
}

impl CortexMcpServer {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_flavor(flavor: ServerFlavor) -> Self {
        Self { flavor }
    }
}

/// One deterministic tool call, from arguments to the wire result.
pub fn call_tool_payload(tool_name: &str, arguments: &Value) -> CallToolResult {
    // FastMCP-level exceptions surface as bare error content (no envelope,
    // no `_meta`) — pydantic signature validation and tool raises.
    if let dispatch::ToolOutcome::RawError { content_text } =
        dispatch::dispatch_tool_outcome(tool_name, arguments)
    {
        let mut result =
            CallToolResult::error(vec![ContentBlock::text(content_text)]);
        result.result_type = None;
        return result;
    }
    let payload = match tool_name {
        "list_mcp_functions" => catalog::list_mcp_functions_payload(),
        "list_parsers" => {
            let detail_level = arguments.get("detail_level").and_then(Value::as_str);
            match dispatch::tool_list_parsers(detail_level) {
                Ok(payload) => payload,
                Err(message) => {
                    // Python raises inside the tool; FastMCP surfaces
                    // `Error calling tool '<tool>': <exc>` as error content
                    // with no structured envelope and no `_meta`.
                    let mut result = CallToolResult::error(vec![ContentBlock::text(format!(
                        "Error calling tool '{tool_name}': {message}"
                    ))]);
                    result.result_type = None;
                    return result;
                }
            }
        }
        _ => match dispatch::dispatch_tool_outcome(tool_name, arguments) {
            dispatch::ToolOutcome::Payload(payload) => payload,
            dispatch::ToolOutcome::RawError { content_text } => {
                let mut result = CallToolResult::error(vec![ContentBlock::text(content_text)]);
                result.result_type = None;
                return result;
            }
            // The unified dispatch never returns prebuilt envelopes; treat
            // defensively as an error result.
            dispatch::ToolOutcome::EnvelopeError { envelope, content_text } => {
                let mut result = CallToolResult::error(vec![ContentBlock::text(content_text)]);
                result.structured_content = Some(envelope);
                result.result_type = None;
                return result;
            }
        },
    };
    let wire = dispatch::wrap_dispatch_result(&payload, tool_name);
    if wire.is_error {
        let mut result = CallToolResult::error(vec![ContentBlock::text(wire.content_text)]);
        result.structured_content = wire.structured_content;
        result.meta = Some(rmcp::model::MetaObject(wire.meta));
        result.result_type = None;
        return result;
    }
    let mut result =
        CallToolResult::structured(wire.structured_content.clone().unwrap_or(Value::Null));
    result.content = vec![ContentBlock::text(wire.content_text)];
    result.meta = Some(rmcp::model::MetaObject(wire.meta));
    result.result_type = None;
    result
}

impl ServerHandler for CortexMcpServer {
    fn get_info(&self) -> ServerInfo {
        let mut capabilities = ServerCapabilities::default();
        capabilities.tools = Some(ToolsCapability::default());
        let mut info = ServerInfo::default();
        info.protocol_version = ProtocolVersion::V_2025_06_18;
        info.capabilities = capabilities;
        info.server_info = Implementation::new(
            self.flavor.server_info_name(),
            match self.flavor {
                // Python: FastMCP(name) — server_version is the fastmcp lib
                // version of the reference environment (mcp_graph_rag.py).
                ServerFlavor::Mind => "1.29.0",
                ServerFlavor::Graph => catalog::unified().server_version.as_str(),
            },
        );
        // The mind server (plain FastMCP) sends no instructions.
        if self.flavor == ServerFlavor::Graph {
            info.instructions = Some(instructions().to_string());
        }
        info
    }

    fn supported_protocol_versions(&self) -> Cow<'static, [ProtocolVersion]> {
        // Keep negotiation at 2025-06-18 (the version the Python
        // `mcp`/`fastmcp` stack serves) so rmcp omits newer-only wire fields
        // such as `resultType`.
        static VERSIONS: [ProtocolVersion; 3] = [
            ProtocolVersion::V_2025_06_18,
            ProtocolVersion::V_2025_03_26,
            ProtocolVersion::V_2024_11_05,
        ];
        Cow::Borrowed(&VERSIONS)
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        _context: RequestContext<RoleServer>,
    ) -> Result<CallToolResponse, McpError> {
        let tool_name = request.name.as_ref().to_string();
        let arguments: Value = Value::Object(request.arguments.unwrap_or_default());
        // Argument validation intentionally bypassed (`get_tool` returns the
        // default `None`): the Python servers validate inside dispatch and
        // return the canonical error envelope, not a transport-level error.
        if self.flavor == ServerFlavor::Mind {
            return Ok(call_mind_tool_payload(&tool_name, &arguments).into());
        }
        Ok(call_tool_payload(&tool_name, &arguments).into())
    }

    async fn list_tools(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListToolsResult, McpError> {
        let tools = match self.flavor {
            ServerFlavor::Graph => served_tools(),
            ServerFlavor::Mind => mind_served_tools(),
        };
        Ok(ListToolsResult {
            result_type: None,
            meta: None,
            next_cursor: None,
            ttl_ms: None,
            cache_scope: None,
            tools,
        })
    }
}

use rmcp::ErrorData as McpError;

/// Helper kept for the contract harness: full call → structured envelope.
pub fn structured_envelope(tool_name: &str, arguments: &Value) -> Value {
    call_tool_payload(tool_name, arguments)
        .structured_content
        .expect("structured envelope")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn server_info_matches_unified() {
        let info = CortexMcpServer::new().get_info();
        assert_eq!(info.server_info.name, "graph_mcp");
        assert_eq!(info.server_info.version, "1.2.0");
        assert!(info
            .instructions
            .unwrap()
            .starts_with("Unified MCP for multi-language code graphs"));
    }

    #[test]
    fn tools_list_matches_catalog_names() {
        let tools = served_tools();
        assert_eq!(tools.len(), 39);
        let names: Vec<&str> = tools.iter().map(|tool| tool.name.as_ref()).collect();
        assert!(names.contains(&"search_functions"));
        assert!(names.contains(&"list_mcp_functions"));
        assert!(!names.contains(&"list_workflows")); // fastmcp-only tool
        let annotate = tools
            .iter()
            .find(|tool| tool.name.as_ref() == "annotate_node")
            .unwrap();
        // annotate_node is proxied → catalog description on the wire.
        assert_eq!(
            annotate.description.as_ref().map(|description| description.as_ref()),
            Some(
                "Add or update annotations (notes/tags/severity) on a node for documentation/review purposes."
            )
        );
        // Required params mirrored onto the schema from the catalog.
        let get_symbol = tools
            .iter()
            .find(|tool| tool.name.as_ref() == "get_symbol")
            .unwrap();
        assert_eq!(get_symbol.input_schema["required"], json!(["node_id"]));
    }

    #[test]
    fn call_tool_list_parsers_success_envelope() {
        let result = call_tool_payload("list_parsers", &json!({"detail_level": "summary"}));
        assert_eq!(result.is_error, Some(false));
        let structured = result.structured_content.unwrap();
        assert_eq!(structured["ok"], json!(true));
        assert_eq!(structured["error"], Value::Null);
        assert_eq!(structured["data"]["detail_level"], json!("summary"));
        let meta = result.meta.unwrap();
        assert_eq!(meta.0["tool"], json!("list_parsers"));
        assert_eq!(meta.0["contract"], json!("cortex.mcp.tool-result"));
        assert_eq!(meta.0["contractVersion"], json!("1.0"));
    }

    #[test]
    fn call_tool_list_parsers_invalid_detail_level() {
        let result = call_tool_payload("list_parsers", &json!({"detail_level": "bogus"}));
        assert_eq!(result.is_error, Some(true));
        assert!(result.structured_content.is_none());
        assert!(result.meta.is_none());
        match &result.content[0] {
            ContentBlock::Text(text) => {
                // FastMCP wraps tool exceptions with the tool name.
                assert_eq!(
                    text.text,
                    "Error calling tool 'list_parsers': \
                     detail_level must be 'summary' or 'full'."
                );
            }
            other => panic!("unexpected content: {other:?}"),
        }
    }

    #[test]
    fn call_tool_list_mcp_functions_matches_catalog() {
        let result = call_tool_payload("list_mcp_functions", &json!({}));
        let structured = result.structured_content.unwrap();
        assert_eq!(structured["data"]["total_count"], json!(39));
        assert_eq!(
            structured["data"]["parameter_guidelines"]["always_call_first"],
            json!("list_mcp_functions")
        );
        match &result.content[0] {
            ContentBlock::Text(text) => {
                assert_eq!(text.text, "Success. Read structuredContent.data.");
            }
            other => panic!("unexpected content: {other:?}"),
        }
    }

    #[test]
    fn reconstruct_flow_envelope_is_canonical() {
        // Phase-12: graph-backed tools dispatch for real; reconstruct_flow is
        // the pure path that proves the success envelope on the wire.
        let result = call_tool_payload(
            "reconstruct_flow",
            &json!({
                "entry_context_json": "{\"type\":\"backend\",\"entry_point\":\"main\",\"entry_node_id\":\"main\"}",
                "paths_json": "[{\"path_id\":\"p1\",\"nodes\":[{\"node_id\":\"main\",\"name\":\"main\",\"mapped_type\":\"function\"}],\"edges\":[]}]",
            }),
        );
        assert_eq!(result.is_error, Some(false));
        let structured = result.structured_content.unwrap();
        assert_eq!(structured["ok"], json!(true));
        assert_eq!(structured["error"], Value::Null);
        assert_eq!(
            structured["data"]["flows"][0]["entry_node_id"],
            json!("main")
        );
        assert_eq!(structured["data"]["uncertainties"], json!([]));
        let meta = result.meta.unwrap();
        assert_eq!(meta.0["tool"], json!("reconstruct_flow"));
        assert_eq!(meta.0["contract"], json!("cortex.mcp.tool-result"));
        assert_eq!(meta.0["contractVersion"], json!("1.0"));
    }
}

//! Port của pipeline dispatch `unified_mcp.py` cho phần deterministic
//! pre-graph (đúng thứ tự Python):
//!
//! 1. `_apply_unified_defaults` — alias `db` → `project_id`.
//! 2. `_coerce_list_fields` — alias/list coercion.
//! 3. `_missing_required_params` pre-flight → `_build_tool_error`.
//! 4. `_normalize_parser_type` + `capability_for_parser` →
//!    `_unsupported_parser_result`.
//! 5. Project registry resolution (strict; phase-11 decision) →
//!    `project_not_registered`.
//! 6. Graph-backed tools → phase-11 stub envelope (`capability_unavailable`).
//! 7. Planner tools (`compute_scc`/`topological_sort`) execute locally.
//!
//! `_wrap_dispatch_result` (middleware) is ported as
//! [`wrap_dispatch_result`], producing the canonical
//! `cortex.mcp.tool-result` v1.0 envelope plus `_meta`, content text and
//! `isError` exactly as the Python `fastmcp` middleware does.

use serde_json::{json, Map, Value};

use crate::catalog;
use crate::contract::{
    canonical_error_code, normalize_error, result_meta, result_summary, ErrorValue,
};
use crate::framework_registry::{capability_for_parser, query_engine_for_backend};
use crate::project_registry::ProjectRegistryError;

/// `_CATALOG_PARAM_DESCRIPTIONS` — descriptions for parameters that appear in
/// registered tool signatures but have no hand-written catalog entry.
pub const CATALOG_PARAM_DESCRIPTIONS: [(&str, &str); 9] = [
    ("parser_type", "Parser profile to route the query (see list_parsers)."),
    (
        "payload",
        "Optional dict merged over the typed parameters (escape hatch).",
    ),
    ("rel_types", "Relationship types to traverse (default: CALLS)."),
    (
        "relationship_types",
        "Relationship types to traverse (alias of rel_types).",
    ),
    ("top_k", "Alias of limit (max results)."),
    ("limit", "Max results."),
    ("debug", "Include debugging details in the response."),
    ("include_possible", "Include POSSIBLE_CALLS edges."),
    ("include_fp", "Include CALLS_FUNCTION_POINTER edges."),
];

/// `_apply_unified_defaults` — `db` is a documented alias of `project_id`.
pub fn apply_unified_defaults(payload: &Value) -> Value {
    let mut merged = payload.as_object().cloned().unwrap_or_default();
    let project_id_empty = merged
        .get("project_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .unwrap_or_default()
        .is_empty();
    if project_id_empty
        && let Some(db_value) = merged.get("db").and_then(Value::as_str)
            .map(str::trim)
            .filter(|text| !text.is_empty())
    {
        merged.insert("project_id".to_string(), json!(db_value));
    }
    Value::Object(merged)
}

/// `_normalize_string_list` (unified variant: returns `None` for absent).
pub fn normalize_string_list(value: Option<&Value>) -> Option<Vec<String>> {
    match value {
        None => None,
        Some(Value::Null) => None,
        Some(Value::String(text)) => {
            let text = text.trim();
            if text.is_empty() {
                return Some(Vec::new());
            }
            if text.contains(',') || text.contains(';') {
                Some(
                    text.replace(';', ",")
                        .split(',')
                        .map(str::trim)
                        .filter(|part| !part.is_empty())
                        .map(str::to_string)
                        .collect(),
                )
            } else {
                Some(vec![text.to_string()])
            }
        }
        Some(Value::Array(items)) => Some(
            items
                .iter()
                .filter_map(|item| match item {
                    Value::String(text) => Some(text.clone()),
                    Value::Number(number) => Some(number.to_string()),
                    Value::Bool(flag) => Some(flag.to_string()),
                    _ => None,
                })
                .map(|item| item.trim().to_string())
                .filter(|item| !item.is_empty())
                .collect(),
        ),
        Some(value) => {
            let text = scalar_to_python_string(value);
            let trimmed = text.trim().to_string();
            Some(if trimmed.is_empty() { Vec::new() } else { vec![trimmed] })
        }
    }
}

fn scalar_to_python_string(value: &Value) -> String {
    match value {
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::String(text) => text.clone(),
        Value::Number(number) => number.to_string(),
        Value::Null => "None".to_string(),
        other => other.to_string(),
    }
}

/// `_coerce_list_fields` — alias pairs + list normalization.
pub fn coerce_list_fields(payload: &Value) -> Value {
    let mut merged = payload.as_object().cloned().unwrap_or_default();

    const ALIAS_PAIRS: [(&str, &str); 6] = [
        ("module", "modules"),
        ("source_module", "source_modules"),
        ("target_module", "target_modules"),
        ("class_name", "class_names"),
        ("file_path", "file_paths"),
        ("relationship_types", "rel_types"),
    ];
    for (source, destination) in ALIAS_PAIRS {
        if !merged.contains_key(destination)
            && let Some(value) = merged.get(source)
        {
            merged.insert(destination.to_string(), value.clone());
        }
    }

    for key in [
        "modules",
        "source_modules",
        "target_modules",
        "class_names",
        "file_paths",
        "rel_types",
        "node_ids",
    ] {
        if let Some(normalized) = merged.get(&key.to_string()).and_then(|value| normalize_string_list(Some(value))) {
            merged.insert(key.to_string(), json!(normalized));
        }
    }

    Value::Object(merged)
}

/// `_normalize_parser_type`.
pub fn normalize_parser_type(value: Option<&str>) -> Option<String> {
    let text = value?.trim().to_lowercase();
    if text.is_empty() {
        None
    } else {
        Some(text)
    }
}

/// `_is_missing_value`.
pub fn is_missing_value(value: &Value) -> bool {
    match value {
        Value::Null => true,
        Value::String(text) => text.trim().is_empty(),
        Value::Array(items) => items.is_empty(),
        Value::Object(object) => object.is_empty(),
        _ => false,
    }
}

/// `_missing_required_params`.
pub fn missing_required_params(tool_name: &str, payload: &Value) -> Vec<String> {
    let arguments = payload.as_object();
    catalog::required_params(tool_name)
        .into_iter()
        .filter(|name| {
            arguments
                .and_then(|arguments| arguments.get(name))
                .map(is_missing_value)
                .unwrap_or(true)
        })
        .collect()
}

/// `_parser_aware_example` — re-target the catalog example at the caller's
/// registered parser. Absent or unregistered parsers keep the catalog
/// example untouched.
pub fn parser_aware_example(tool_name: &str, payload: &Value) -> Value {
    let Some(example) = catalog::catalog_example(tool_name) else {
        return Value::Null;
    };
    let Some(example_text) = example.as_str() else {
        return example.clone();
    };
    if !example_text.contains("parser_type='") {
        return example.clone();
    }
    let parser = normalize_parser_type(
        payload
            .get("parser_type")
            .and_then(Value::as_str),
    );
    let Some(parser) = parser else {
        return example.clone();
    };
    if capability_for_parser(Some(&parser)).is_none() {
        return example.clone();
    }
    let current = extract_parser_type(example_text);
    if current.as_deref() == Some(parser.as_str()) {
        return example.clone();
    }
    let updated = replace_parser_type(example_text, &parser);
    let language = payload.get("language").and_then(Value::as_str);
    match language.map(str::trim).filter(|text| !text.is_empty()) {
        Some(language) => Value::String(replace_language(&updated, language)),
        None => Value::String(remove_language(&updated)),
    }
}

fn extract_parser_type(example: &str) -> Option<String> {
    let start = example.find("parser_type='")? + "parser_type='".len();
    let rest = &example[start..];
    let end = rest.find('\'')?;
    Some(rest[..end].to_string())
}

fn replace_parser_type(example: &str, parser: &str) -> String {
    // Python: re.sub(r"parser_type='[^']*'", f"parser_type='{parser}'", ...)
    let mut out = String::with_capacity(example.len());
    let mut rest = example;
    while let Some(position) = rest.find("parser_type='") {
        out.push_str(&rest[..position]);
        let remainder = &rest[position + "parser_type='".len()..];
        match remainder.find('\'') {
            Some(end) => {
                out.push_str(&format!("parser_type='{parser}'"));
                rest = &remainder[end + 1..];
            }
            None => {
                out.push_str("parser_type='");
                rest = remainder;
                break;
            }
        }
    }
    out.push_str(rest);
    out
}

fn replace_language(example: &str, language: &str) -> String {
    let mut out = String::with_capacity(example.len());
    let mut rest = example;
    while let Some(position) = rest.find("language='") {
        out.push_str(&rest[..position]);
        let remainder = &rest[position + "language='".len()..];
        match remainder.find('\'') {
            Some(end) => {
                out.push_str(&format!("language='{language}'"));
                rest = &remainder[end + 1..];
            }
            None => {
                out.push_str("language='");
                rest = remainder;
                break;
            }
        }
    }
    out.push_str(rest);
    out
}

fn remove_language(example: &str) -> String {
    // Python: re.sub(r", language='[^']*'", "", updated)
    let mut out = String::with_capacity(example.len());
    let mut rest = example;
    while let Some(position) = rest.find(", language='") {
        out.push_str(&rest[..position]);
        let remainder = &rest[position + ", language='".len()..];
        match remainder.find('\'') {
            Some(end) => {
                rest = &remainder[end + 1..];
            }
            None => {
                out.push_str(", language='");
                rest = remainder;
                break;
            }
        }
    }
    out.push_str(rest);
    out
}

/// `_error_type_from_exception`.
pub fn error_type_from_exception(exception_name: &str, missing_required: &[String]) -> &'static str {
    if !missing_required.is_empty() {
        return "missing_required_parameters";
    }
    match exception_name {
        "ValueError" | "TypeError" => "invalid_parameters",
        _ => "tool_execution_error",
    }
}

/// `_build_tool_error` — the legacy unified error payload.
pub fn build_tool_error(tool_name: &str, payload: &Value, message: &str) -> Value {
    let arguments = payload.as_object().cloned().unwrap_or_default();
    let missing_required = missing_required_params(tool_name, payload);
    let received: Vec<String> = arguments
        .iter()
        .filter(|(_, value)| !is_missing_value(value))
        .map(|(key, _)| key.clone())
        .collect();
    let example = parser_aware_example(tool_name, payload);
    let error_type = error_type_from_exception("ValueError", &missing_required);
    let query_engine = query_engine_for_backend(None);
    json!({
        "ok": false,
        "query_engine": query_engine,
        "error": {
            "type": error_type,
            "tool": tool_name,
            "query_engine": query_engine,
            "message": message,
            "missing_required_params": missing_required,
            "required_params": catalog::required_params(tool_name),
            "accepted_params": catalog::accepted_params(tool_name),
            "received_params": received,
            "example": example,
            "next_step": "Call list_mcp_functions and retry with exact parameter names.",
        },
    })
}

/// `_unsupported_parser_result` — legacy payload for an explicitly named
/// parser that is not registered.
pub fn unsupported_parser_result(tool_name: &str, payload: &Value, parser_type: &str) -> Value {
    let parser = normalize_parser_type(Some(parser_type)).unwrap_or_default();
    let mut error = build_tool_error(
        tool_name,
        payload,
        &format!("Parser '{parser}' is not registered."),
    );
    if let Some(error_object) = error.get_mut("error").and_then(Value::as_object_mut) {
        error_object.insert("type".to_string(), json!("unsupported_parser"));
        error_object.insert("parser_type".to_string(), json!(parser));
        error_object.insert(
            "supported_parsers".to_string(),
            json!(crate::framework_registry::capability_catalog()
                .iter()
                .filter_map(|capability| capability.get("canonical_parser").cloned())
                .collect::<Vec<_>>()),
        );
        error_object.insert(
            "supported_aliases".to_string(),
            json!(crate::framework_registry::parser_aliases(None)),
        );
        error_object.insert(
            "next_step".to_string(),
            json!("Call list_parsers and retry with a canonical parser or registered alias."),
        );
    }
    error
}

/// Message template for phase-11 graph-backed tool stubs. The canonical
/// envelope for this message is golden-fixed via the Python contract layer
/// (see `scripts/rust_mcp/record_contract.py`).
pub fn graph_stub_message(tool_name: &str) -> String {
    format!(
        "Tool '{tool_name}' requires the graph query engine, which is not part of the \
         phase-11 MCP framework build; graph tools land in phase 12."
    )
}

/// Unified-defined tools whose *registered signature* requires a parameter
/// (no Python default). FastMCP validates these at the transport boundary —
/// a missing argument becomes a pydantic validation error before the tool
/// body (and the middleware envelope) ever runs.
pub const SIGNATURE_REQUIRED_PARAMS: [&str; 2] = [
    "analyze_workflow_impact",
    "find_workflows_containing",
];

/// FastMCP/pydantic transport-level error text for a missing required
/// signature argument (byte-matched against the recorded live fixture).
pub fn fastmcp_missing_argument_text(tool_name: &str, parameter: &str) -> String {
    format!(
        "1 validation error for call[tool_{tool_name}]\n{parameter}\n  \
         Missing required argument [type=missing_argument, input_value={{}}, \
         input_type=dict]\n    For further information visit \
         https://errors.pydantic.dev/2.13/v/missing_argument"
    )
}

/// The dispatch outcome for one tool call, mirroring where the Python server
/// produces it: a transport-level error (FastMCP exception) or the legacy
/// payload dict that the middleware wraps into the canonical envelope.
#[derive(Debug, Clone)]
pub enum ToolOutcome {
    /// FastMCP surfaces `str(exc)` with no structured envelope and no `_meta`.
    RawError { content_text: String },
    /// Legacy payload dict → `wrap_dispatch_result`.
    Payload(Value),
}

/// Signature-level validation (FastMCP layer, before the middleware).
fn signature_required_guard(tool_name: &str, arguments: &Value) -> Option<ToolOutcome> {
    if !SIGNATURE_REQUIRED_PARAMS.contains(&tool_name) {
        return None;
    }
    let required = match tool_name {
        "analyze_workflow_impact" | "find_workflows_containing" => "function_id",
        _ => return None,
    };
    let missing = arguments
        .get(required)
        .map(is_missing_value)
        .unwrap_or(true);
    missing.then(|| ToolOutcome::RawError {
        content_text: fastmcp_missing_argument_text(tool_name, required),
    })
}

/// Deterministic pre-graph guard branches of the unified-defined tools whose
/// registered signatures take defaults (they run their own bodies instead of
/// the catalog preflight).
fn direct_tool_guard(tool_name: &str, arguments: &Value) -> Option<ToolOutcome> {
    let arguments = arguments.as_object()?;
    match tool_name {
        "explore_graph" => {
            let query = arguments
                .get("query")
                .and_then(Value::as_str)
                .map(str::trim)
                .unwrap_or_default();
            if query.is_empty() {
                let mode = arguments
                    .get("mode")
                    .and_then(Value::as_str)
                    .unwrap_or("hybrid");
                return Some(ToolOutcome::Payload(json!({
                    "matched_nodes": [],
                    "entry_points": [],
                    "related_paths": [],
                    "explanation": "No query provided.",
                    "confidence": 0.0,
                    "query_analysis": {},
                    "mode": mode,
                })));
            }
            None
        }
        "reconstruct_flow" => {
            let entry_context = arguments
                .get("entry_context_json")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let paths = arguments
                .get("paths_json")
                .and_then(Value::as_str)
                .unwrap_or_default();
            if entry_context.is_empty() || paths.is_empty() {
                return Some(ToolOutcome::Payload(json!({
                    "flows": [],
                    "uncertainties": [
                        "entry_context_json and paths_json are required"
                    ],
                })));
            }
            None
        }
        "find_callers_of_endpoint" => {
            let endpoint_path = arguments
                .get("endpoint_path")
                .and_then(Value::as_str)
                .unwrap_or_default();
            if endpoint_path.is_empty() {
                return Some(ToolOutcome::Payload(json!({
                    "endpoint_path": "",
                    "callers": [],
                    "total": 0,
                    "error": "endpoint_path is required",
                })));
            }
            None
        }
        _ => None,
    }
}

/// Full unified dispatch for one tool call.
pub fn dispatch_tool_outcome(tool_name: &str, arguments: &Value) -> ToolOutcome {
    // 0. FastMCP signature validation (before the tool body / middleware).
    if let Some(outcome) = signature_required_guard(tool_name, arguments) {
        return outcome;
    }
    // 0b. Direct unified tools run their own bodies: deterministic guards
    // fire before the catalog preflight (which only exists for proxied
    // tools inside `_dispatch_tool`).
    if let Some(outcome) = direct_tool_guard(tool_name, arguments) {
        return outcome;
    }
    ToolOutcome::Payload(dispatch_tool(tool_name, arguments))
}

/// The unified dispatch outcome for one tool call: the legacy payload dict
/// the Python dispatch would return (before middleware wrapping).
pub fn dispatch_tool(tool_name: &str, arguments: &Value) -> Value {
    // 1. defaults + 2. coercion.
    let merged = apply_unified_defaults(arguments);
    let merged = coerce_list_fields(&merged);

    // 3. missing-required pre-flight.
    let missing_required = missing_required_params(tool_name, &merged);
    if !missing_required.is_empty() {
        let joined = missing_required.join(", ");
        return build_tool_error(
            tool_name,
            &merged,
            &format!("Missing required parameters: {joined}"),
        );
    }

    // 4. parser resolution.
    let selected_parser = normalize_parser_type(merged.get("parser_type").and_then(Value::as_str));
    let capability = capability_for_parser(selected_parser.as_deref());
    if let Some(parser) = &selected_parser
        && capability.is_none()
    {
        return unsupported_parser_result(tool_name, &merged, parser);
    }

    // 5. planner tools execute locally (no graph).
    match tool_name {
        "compute_scc" => {
            return crate::planner::dispatch_planner_result(
                tool_name,
                &merged,
                crate::planner::tool_compute_scc(&merged),
            );
        }
        "topological_sort" => {
            return crate::planner::dispatch_planner_result(
                tool_name,
                &merged,
                crate::planner::tool_topological_sort(&merged),
            );
        }
        _ => {}
    }

    // 5b. project scope resolution — phase-12 decision: graph-backed tools
    // resolve unregistered project ids qua naming convention
    // (`code_graph == project_id`) đúng như `_resolve_db_candidates` của
    // Python; gate strict phase-11 chỉ tồn tại cho stub envelope, đã bỏ.
    let _ = ProjectRegistryError::NotRegistered;

    // 6. list_parsers / list_mcp_functions are served natively by the server
    // (see `server.rs`); graph-backed tools dispatch into the phase-12 graph
    // layer, falling back to the phase-11 stub for anything unported.
    match crate::graph::dispatch_graph_tool(tool_name, &merged) {
        Some(payload) => payload,
        None => json!({
            "ok": false,
            "error": {
                "type": "capability_unavailable",
                "tool": tool_name,
                "message": graph_stub_message(tool_name),
            },
        }),
    }
}

/// `tool_list_parsers` (unified port, pure).
pub fn tool_list_parsers(detail_level: Option<&str>) -> Result<Value, String> {
    let normalized_detail = detail_level
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .unwrap_or("summary")
        .to_lowercase();
    if normalized_detail != "summary" && normalized_detail != "full" {
        return Err("detail_level must be 'summary' or 'full'.".to_string());
    }
    let full_capabilities = crate::framework_registry::capability_catalog();
    let capabilities = if normalized_detail == "full" {
        full_capabilities
    } else {
        const SUMMARY_FIELDS: [&str; 6] = [
            "canonical_parser",
            "aliases",
            "query_engine",
            "support_level",
            "support",
            "generation_scoped",
        ];
        full_capabilities
            .iter()
            .map(|capability| {
                let fields = capability.as_object().expect("capability object");
                let mut summary = Map::new();
                for field in SUMMARY_FIELDS {
                    if let Some(value) = fields.get(field) {
                        summary.insert(field.to_string(), value.clone());
                    }
                }
                Value::Object(summary)
            })
            .collect()
    };
    Ok(json!({
        "parsers": crate::framework_registry::parser_aliases(None),
        "capabilities": capabilities,
        "detail_level": normalized_detail,
        "capability_contract_version": crate::framework_registry::CAPABILITY_CONTRACT_VERSION,
        "default_query_engine": query_engine_for_backend(Some(crate::framework_registry::default_backend())),
        "active_parser_type": Value::Null,
        "active_capability": capability_summary(None),
    }))
}

/// `_capability_summary(None, None)` — the implicit full-search routing
/// summary (parser omitted → no warning).
pub fn capability_summary(parser_type: Option<&str>) -> Value {
    let parser = parser_type.map(|value| value.trim().to_lowercase()).filter(|value| !value.is_empty());
    if let Some(capability) = capability_for_parser(parser.as_deref()) {
        let support: Map<String, Value> = capability
            .support
            .iter()
            .map(|(key, value)| (key.clone(), json!(value)))
            .collect();
        return json!({
            "requested_parser": parser,
            "canonical_parser": capability.name,
            "query_engine": query_engine_for_backend(Some(&capability.backend)),
            "support_level": capability.support_level,
            "support": Value::Object(support),
        });
    }
    let generic_support = json!({
        "symbols": "generic",
        "calls": "generic",
        "endpoints": "none",
        "database": "none",
    });
    if let Some(parser) = parser {
        return json!({
            "requested_parser": parser,
            "canonical_parser": Value::Null,
            "query_engine": query_engine_for_backend(None),
            "support_level": "generic",
            "support": generic_support,
            "warning": format!(
                "Parser '{parser}' is not registered; generic query behavior is being used."
            ),
        });
    }
    json!({
        "requested_parser": Value::Null,
        "canonical_parser": Value::Null,
        "query_engine": query_engine_for_backend(None),
        "support_level": "generic",
        "support": generic_support,
    })
}

/// Result of the middleware wrap: exactly what lands on the MCP wire for a
/// `tools/call` response.
#[derive(Debug, Clone)]
pub struct ToolCallWire {
    pub structured_content: Option<Value>,
    pub content_text: String,
    pub is_error: bool,
    pub meta: Map<String, Value>,
}

/// `_wrap_dispatch_result` — canonical envelope + meta + bounded content.
pub fn wrap_dispatch_result(payload: &Value, tool_name: &str) -> ToolCallWire {
    let meta = result_meta(Some(tool_name));
    let is_error = payload.as_object().map(|object| object.get("ok") == Some(&json!(false))).unwrap_or(false);

    if is_error {
        let envelope = normalize_error(ErrorValue::Legacy(payload), None, None, None, None);
        let message = envelope["error"]["message"]
            .as_str()
            .unwrap_or("Tool execution failed.")
            .to_string();
        return ToolCallWire {
            structured_content: Some(envelope),
            content_text: result_summary(None, false, Some(&message)),
            is_error: true,
            meta,
        };
    }

    let mut payload_value = payload.clone();
    if let Some(object) = payload_value.as_object_mut()
        && object.get("ok") == Some(&json!(true))
    {
        object.remove("ok");
    }
    let envelope = normalize_success_value(&payload_value);
    let content_text = result_summary(Some(&payload_value), true, None);
    ToolCallWire {
        structured_content: Some(envelope),
        content_text,
        is_error: false,
        meta,
    }
}

/// `normalize_success` exposed for the middleware (keeps import surface
/// identical to `contract`).
pub fn normalize_success_value(data: &Value) -> Value {
    crate::contract::normalize_success(data.clone())
}

/// Canonical error code helper re-exported for tests/fixturing.
pub fn canonical_code(code: Option<&str>) -> String {
    canonical_error_code(code)
}

/// Sorted accepted/required snapshot for a tool (used by tooling/tests).
pub fn tool_parameter_snapshot(tool_name: &str) -> (Vec<String>, Vec<String>) {
    (
        catalog::required_params(tool_name),
        catalog::accepted_params(tool_name),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_required_preflight_matches_wire() {
        let wire = wrap_dispatch_result(&dispatch_tool("get_symbol", &json!({})), "get_symbol");
        assert!(wire.is_error);
        let structured = wire.structured_content.unwrap();
        assert_eq!(structured["ok"], json!(false));
        assert_eq!(
            structured["error"]["code"],
            json!("missing_required_parameters")
        );
        assert_eq!(
            structured["error"]["message"],
            json!("Missing required parameters: node_id")
        );
        assert_eq!(
            structured["error"]["details"]["missing_parameters"],
            json!(["node_id"])
        );
        assert_eq!(wire.content_text, "Missing required parameters: node_id");
        assert_eq!(wire.meta["tool"], json!("get_symbol"));
        assert_eq!(wire.meta["contract"], json!("cortex.mcp.tool-result"));
        assert_eq!(wire.meta["contractVersion"], json!("1.0"));
    }

    #[test]
    fn unsupported_parser_before_graph() {
        let arguments = json!({"node_id": "n1", "parser_type": "bogus_parser"});
        let wire = wrap_dispatch_result(&dispatch_tool("get_symbol", &arguments), "get_symbol");
        assert!(wire.is_error);
        let structured = wire.structured_content.unwrap();
        assert_eq!(structured["error"]["code"], json!("unsupported_parser"));
        assert_eq!(structured["error"]["details"], json!({"parser": "bogus_parser"}));
    }

    #[test]
    fn unregistered_project_uses_naming_convention() {
        // Phase-12: graph tools resolve unregistered project ids qua naming
        // convention (code_graph == project_id) như Python
        // `_resolve_db_candidates` — không còn gate strict phase-11.
        let arguments = json!({"node_id": "n1", "project_id": "phase11_unregistered_project"});
        let payload = dispatch_tool("get_symbol", &arguments);
        // get_symbol là fanout tool → per-parser results, ok=true (hit/miss
        // structurally reported), không còn project_not_registered envelope.
        assert_eq!(payload["error"], Value::Null);
        assert_eq!(payload["parsers_searched"], json!(["android", "cplus"]));
    }

    #[test]
    fn db_alias_and_list_coercion() {
        let merged = coerce_list_fields(&apply_unified_defaults(&json!({
            "db": "cortext",
            "module": "a,b",
        })));
        assert_eq!(merged["project_id"], json!("cortext"));
        assert_eq!(merged["modules"], json!(["a", "b"]));
    }

    #[test]
    fn list_parsers_payload_shape() {
        let payload = tool_list_parsers(Some("summary")).unwrap();
        assert_eq!(payload["detail_level"], json!("summary"));
        assert_eq!(payload["active_parser_type"], Value::Null);
        assert_eq!(payload["default_query_engine"], json!("graph_generic"));
        assert_eq!(payload["capability_contract_version"], json!(1));
        assert!(payload["capabilities"].as_array().unwrap().len() == 27);
        assert!(payload["parsers"].as_array().unwrap().iter().any(|parser| parser == "cplus"));
        let error = tool_list_parsers(Some("bogus")).unwrap_err();
        assert_eq!(error, "detail_level must be 'summary' or 'full'.");
    }

    #[test]
    fn parser_aware_example_retargeting() {
        let example = parser_aware_example(
            "get_public_apis",
            &json!({"parser_type": "spring", "language": "java"}),
        );
        let text = example.as_str().unwrap();
        assert!(text.contains("parser_type='spring'"), "{text}");
        assert!(text.contains("language='java'"), "{text}");
        // Unknown parser keeps the catalog example untouched.
        let untouched = parser_aware_example(
            "get_public_apis",
            &json!({"parser_type": "bogus"}),
        );
        assert_eq!(untouched, catalog::catalog_example("get_public_apis").unwrap().clone());
    }

    #[test]
    fn tool_parameter_snapshot_helper() {
        let (required, accepted) = tool_parameter_snapshot("find_paths");
        assert_eq!(required, vec!["start_function_id", "end_function_id"]);
        assert!(accepted.contains(&"project_id".to_string()));
    }
}

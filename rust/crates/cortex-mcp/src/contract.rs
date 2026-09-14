//! Port của `cortex_harness/mcp_contract.py` — shared protocol-boundary
//! contract for Cortex MCP tool results.
//!
//! Business payloads stay tool-specific inside `data`. The wire-facing
//! success/error shape is deliberately small and identical for graph_mcp and
//! mind_mcp so clients never have to infer whether a dict is data or an error.
//!
//! Byte-compatibility notes (vs Python):
//! * object key order is irrelevant on the wire (clients parse JSON maps);
//! * every string, number, bool and null is reproduced exactly;
//! * `_INTERNAL_ERROR_DETAIL_KEYS` filtering drops catalog-sized diagnostics
//!   so error `details` stay stable.

use serde_json::{json, Map, Value};

pub const MCP_CONTRACT_NAME: &str = "cortex.mcp.tool-result";
pub const MCP_CONTRACT_VERSION: &str = "1.0";

/// `_ERROR_CODE_ALIASES` — canonical code for a legacy alias.
fn error_code_alias(normalized: &str) -> Option<&'static str> {
    match normalized {
        "unsupported_capability" => Some("capability_unavailable"),
        "value_error" => Some("invalid_parameters"),
        "type_error" => Some("invalid_parameters"),
        "lookup_error" => Some("collection_unavailable"),
        "project_not_registered_error" => Some("project_not_registered"),
        _ => None,
    }
}

/// `_INTERNAL_ERROR_DETAIL_KEYS` — fields that describe server internals or
/// duplicate information available from discovery tools. Useful in logs and
/// capability inspectors, but they make ordinary tool errors unstable and
/// unnecessarily large.
const INTERNAL_ERROR_DETAIL_KEYS: &[&str] = &[
    "accepted_params",
    "available_labels",
    "available_relationships",
    "capability",
    "capability_diagnostics",
    "context",
    "example",
    "next_step",
    "query_engine",
    "received_params",
    "required_params",
    "supported_aliases",
    "supported_parsers",
    "tool",
];


/// `_canonical_error_code`.
pub fn canonical_error_code(value: Option<&str>) -> String {
    let raw = match value {
        Some(value) if !value.is_empty() => value.trim().to_ascii_lowercase(),
        _ => "tool_execution_error".to_string(),
    };
    let normalized = raw.replace(['-', ' '], "_");
    error_code_alias(&normalized)
        .map(str::to_string)
        .unwrap_or(normalized)
}

/// `normalize_success` — canonical success envelope without copying `data`.
pub fn normalize_success(data: Value) -> Value {
    json!({"ok": true, "data": data, "error": Value::Null})
}

/// Exception class names with a stable contract code (`_exception_code`).
fn stable_exception_code(name: &str) -> Option<&'static str> {
    match name {
        "ValueError" | "TypeError" => Some("invalid_parameters"),
        "LookupError" => Some("collection_unavailable"),
        "ProjectNotRegisteredError" => Some("project_not_registered"),
        "TimeoutError" | "ConnectionError" => Some("storage_unavailable"),
        _ => None,
    }
}

/// `_exception_code`: stable codes first, then CamelCase → snake_case.
pub fn exception_code(exception_name: &str) -> String {
    if let Some(code) = stable_exception_code(exception_name) {
        return code.to_string();
    }
    let mut words: Vec<String> = Vec::new();
    let mut current = String::new();
    for character in exception_name.chars() {
        if character.is_uppercase() && !current.is_empty() {
            words.push(std::mem::take(&mut current));
            current.push(character.to_ascii_lowercase());
        } else {
            current.push(character.to_ascii_lowercase());
        }
    }
    if !current.is_empty() {
        words.push(current);
    }
    if words.is_empty() {
        "tool_execution_error".to_string()
    } else {
        words.join("_")
    }
}

/// `_non_empty`: `value is not None and value != "" and value != [] and value != {}`.
fn non_empty(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
        _ => true,
    }
}

/// `_sanitized_details` — keep intentional details while excluding
/// catalog-sized diagnostics.
fn sanitized_details(details: Option<&Map<String, Value>>) -> Map<String, Value> {
    let mut result = Map::new();
    if let Some(details) = details {
        for (key, item) in details {
            if !INTERNAL_ERROR_DETAIL_KEYS.contains(&key.as_str()) && non_empty(item) {
                result.insert(key.clone(), item.clone());
            }
        }
    }
    result
}

fn as_object(value: Option<&Value>) -> Option<&Map<String, Value>> {
    value.and_then(Value::as_object)
}

/// `_compact_legacy_details` — extract only stable, actionable fields from a
/// legacy error payload.
///
/// Legacy graph errors carry parameter help plus complete provider schemas.
/// Those catalogs belong in discovery/inspection tools, not every failed
/// response. This boundary deliberately uses an allowlist instead of copying
/// unknown fields.
fn compact_legacy_details(
    error_code: &str,
    raw_error: &Map<String, Value>,
    outer: &Map<String, Value>,
) -> Map<String, Value> {
    let mut compact = Map::new();

    match raw_error.get("details") {
        Some(Value::Object(embedded)) => {
            for (key, value) in sanitized_details(Some(embedded)) {
                compact.insert(key, value);
            }
        }
        Some(details @ Value::Array(items)) if !items.is_empty() => {
            compact.insert("violations".to_string(), details.clone());
        }
        _ => {}
    }

    if error_code == "capability_unavailable" {
        let capability = as_object(outer.get("capability")).cloned().unwrap_or_default();
        let diagnostics = as_object(outer.get("capability_diagnostics"))
            .cloned()
            .unwrap_or_default();

        let parser = raw_error
            .get("parser_type")
            .or_else(|| capability.get("requested_parser"));
        let missing_relationships = raw_error
            .get("missing_relationships")
            .or_else(|| raw_error.get("missing_required_relationships"))
            .or_else(|| diagnostics.get("missing_required_relationships"));
        let missing_labels = raw_error
            .get("missing_labels")
            .or_else(|| raw_error.get("missing_required_labels"))
            .or_else(|| diagnostics.get("missing_required_labels"));
        if let Some(parser) = parser
            && non_empty(parser)
        {
            compact.insert("parser".to_string(), parser.clone());
        }
        if let Some(missing) = missing_relationships
            && non_empty(missing)
        {
            compact.insert("missing_relationships".to_string(), missing.clone());
        }
        if let Some(missing) = missing_labels
            && non_empty(missing)
        {
            compact.insert("missing_labels".to_string(), missing.clone());
        }
    }

    if error_code == "unsupported_parser"
        && non_empty(raw_error.get("parser_type").unwrap_or(&Value::Null))
    {
        compact.insert(
            "parser".to_string(),
            raw_error.get("parser_type").cloned().unwrap_or(Value::Null),
        );
    }

    if let Some(missing) = raw_error.get("missing_required_params")
        && non_empty(missing)
    {
        compact.insert("missing_parameters".to_string(), missing.clone());
    }

    // Small operational fields let clients decide whether/how to retry while
    // avoiding the full backend state snapshot.
    for key in [
        "retry_after_ms",
        "correlation_id",
        "capacity",
        "accepted_limit",
        "project_id",
        "collection",
        "parameter",
        "field",
    ] {
        if let Some(item) = raw_error.get(key)
            && non_empty(item)
        {
            compact.insert(key.to_string(), item.clone());
        }
    }

    compact
}

/// Input for [`normalize_error`] — mirrors the Python `value` parameter:
/// `None`, a `BaseException` (typed by its class name here) or a legacy
/// `Mapping` payload.
#[derive(Debug, Clone)]
pub enum ErrorValue<'a> {
    /// `normalize_error()` — no value at all.
    None,
    /// A `BaseException`: (class name, `str(exc)`).
    Exception { name: &'a str, message: Option<&'a str> },
    /// A legacy mapping payload (`{"ok": false, "error": {...}}`).
    Legacy(&'a Value),
}

/// `normalize_error` — convert legacy errors or exceptions to the canonical
/// error envelope.
pub fn normalize_error(
    value: ErrorValue<'_>,
    code: Option<&str>,
    message: Option<&str>,
    retryable: Option<bool>,
    details: Option<&Map<String, Value>>,
) -> Value {
    let mut resolved_code = code.map(str::to_string);
    let mut resolved_message = message.map(str::to_string);
    let mut resolved_retryable = retryable;
    let mut legacy_details = Map::new();

    match &value {
        ErrorValue::None => {}
        ErrorValue::Exception { name, message } => {
            if resolved_code.is_none() {
                resolved_code = Some(exception_code(name));
            }
            if resolved_message.is_none() {
                resolved_message = Some(
                    message
                        .filter(|text| !text.is_empty())
                        .map(str::to_string)
                        .unwrap_or_else(|| name.to_string()),
                );
            }
            if resolved_retryable.is_none() && matches!(*name, "TimeoutError" | "ConnectionError")
            {
                resolved_retryable = Some(true);
            }
        }
        ErrorValue::Legacy(payload) => {
            if let Some(payload_object) = payload.as_object() {
                match payload_object.get("error") {
                    Some(Value::Object(raw_error_object)) => {
                        if resolved_code.is_none() {
                            resolved_code = Some(canonical_error_code(
                                raw_error_object
                                    .get("code")
                                    .and_then(Value::as_str)
                                    .or_else(|| raw_error_object.get("type").and_then(Value::as_str))
                                    .or(Some("tool_execution_error")),
                            ));
                        }
                        if resolved_message.is_none() {
                            resolved_message = Some(
                                raw_error_object
                                    .get("message")
                                    .and_then(Value::as_str)
                                    .filter(|text| !text.is_empty())
                                    .map(str::to_string)
                                    .unwrap_or_else(|| "Tool execution failed.".to_string()),
                            );
                        }
                        if resolved_retryable.is_none() {
                            resolved_retryable = Some(
                                raw_error_object
                                    .get("retryable")
                                    .and_then(Value::as_bool)
                                    .unwrap_or(false),
                            );
                        }
                        let canonical = canonical_error_code(resolved_code.as_deref());
                        legacy_details = compact_legacy_details(
                            &canonical,
                            raw_error_object,
                            payload_object,
                        );
                    }
                    Some(raw_error) if resolved_message.is_none() => {
                        // Python `str(raw_error)` for any non-mapping error value.
                        resolved_message = Some(match raw_error {
                            Value::String(text) => text.clone(),
                            Value::Bool(true) => "True".to_string(),
                            Value::Bool(false) => "False".to_string(),
                            Value::Number(number) => number.to_string(),
                            _ => raw_error.to_string(),
                        });
                    }
                    Some(_) => {}
                    None => {}
                }
            }
        }
    }

    let mut merged_details = legacy_details;
    for (key, value) in sanitized_details(details) {
        merged_details.insert(key, value);
    }

    json!({
        "ok": false,
        "data": Value::Null,
        "error": {
            "code": canonical_error_code(resolved_code.as_deref()),
            "message": resolved_message.unwrap_or_else(|| "Tool execution failed.".to_string()),
            "retryable": resolved_retryable.unwrap_or(false),
            "details": Value::Object(merged_details),
        },
    })
}

/// `result_meta` — metadata placed in MCP `_meta`, not in the business payload.
pub fn result_meta(tool_name: Option<&str>) -> Map<String, Value> {
    let mut meta = Map::new();
    meta.insert("contract".to_string(), json!(MCP_CONTRACT_NAME));
    meta.insert("contractVersion".to_string(), json!(MCP_CONTRACT_VERSION));
    meta.insert(
        "tool".to_string(),
        json!(tool_name.unwrap_or("unknown")),
    );
    meta
}

/// `result_summary` — build bounded human content while full data stays
/// structured.
pub fn result_summary(data: Option<&Value>, ok: bool, message: Option<&str>) -> String {
    if !ok {
        return message
            .map(str::to_string)
            .unwrap_or_else(|| "Tool execution failed. See structuredContent.error.".to_string());
    }

    let mut count: Option<usize> = None;
    let mut label = "item";
    if let Some(data) = data.and_then(Value::as_object) {
        for (key, singular) in [
            ("results", "result"),
            ("passages", "passage"),
            ("items", "item"),
            ("ids", "ID"),
        ] {
            if let Some(Value::Array(items)) = data.get(key) {
                count = Some(items.len());
                label = singular;
                break;
            }
        }
    } else if let Some(Value::Array(items)) = data {
        count = Some(items.len());
    }

    let Some(count) = count else {
        return "Success. Read structuredContent.data.".to_string();
    };
    let suffix = if count == 1 {
        label.to_string()
    } else {
        format!("{label}s")
    };
    format!("Success: {count} {suffix}. Read structuredContent.data.")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::from_value;

    fn error_of(envelope: Value) -> (String, String, bool, Map<String, Value>) {
        let object = envelope.as_object().unwrap();
        assert_eq!(object.get("ok"), Some(&json!(false)));
        assert_eq!(object.get("data"), Some(&Value::Null));
        let error = object.get("error").unwrap().as_object().unwrap();
        (
            error["code"].as_str().unwrap().to_string(),
            error["message"].as_str().unwrap().to_string(),
            error["retryable"].as_bool().unwrap(),
            error["details"].as_object().unwrap().clone(),
        )
    }

    #[test]
    fn canonical_code_applies_aliases() {
        assert_eq!(canonical_error_code(Some("unsupported_capability")), "capability_unavailable");
        assert_eq!(canonical_error_code(Some("Value-Error")), "invalid_parameters");
        assert_eq!(canonical_error_code(Some("project not registered error")), "project_not_registered");
        assert_eq!(canonical_error_code(Some("missing_required_parameters")), "missing_required_parameters");
        assert_eq!(canonical_error_code(Some("")), "tool_execution_error");
        assert_eq!(canonical_error_code(None), "tool_execution_error");
    }

    #[test]
    fn exception_codes_match_python() {
        assert_eq!(exception_code("ValueError"), "invalid_parameters");
        assert_eq!(exception_code("ProjectNotRegisteredError"), "project_not_registered");
        assert_eq!(exception_code("TimeoutError"), "storage_unavailable");
        assert_eq!(exception_code("RuntimeError"), "runtime_error");
        assert_eq!(exception_code("KeyError"), "key_error");
    }

    #[test]
    fn success_envelope_shape() {
        let envelope = normalize_success(json!({"db": "cortext", "results": []}));
        assert_eq!(
            envelope,
            json!({"ok": true, "data": {"db": "cortext", "results": []}, "error": null})
        );
    }

    #[test]
    fn legacy_missing_required_maps_like_python() {
        // Mirrors tests/mcp parity for `_wrap_dispatch_result` on a
        // `_build_tool_error` payload (missing required parameter).
        let legacy = json!({
            "ok": false,
            "query_engine": "graph_generic",
            "error": {
                "type": "missing_required_parameters",
                "tool": "get_symbol",
                "query_engine": "graph_generic",
                "message": "Missing required parameters: node_id",
                "missing_required_params": ["node_id"],
                "required_params": ["node_id"],
                "accepted_params": ["node_id", "project_id"],
                "received_params": [],
                "example": "get_symbol(node_id='func_12345')",
                "next_step": "Call list_mcp_functions and retry with exact parameter names.",
            },
        });
        let (code, message, retryable, details) = error_of(normalize_error(
            ErrorValue::Legacy(&legacy),
            None,
            None,
            None,
            None,
        ));
        assert_eq!(code, "missing_required_parameters");
        assert_eq!(message, "Missing required parameters: node_id");
        assert!(!retryable);
        assert_eq!(details.get("missing_parameters"), Some(&json!(["node_id"])));
        // Catalog-sized diagnostics are filtered out.
        assert!(details.get("required_params").is_none());
        assert!(details.get("example").is_none());
    }

    #[test]
    fn legacy_unsupported_parser_maps_like_python() {
        let legacy = json!({
            "ok": false,
            "query_engine": "graph_generic",
            "error": {
                "type": "unsupported_parser",
                "tool": "get_symbol",
                "query_engine": "graph_generic",
                "message": "Parser 'bogus_parser' is not registered.",
                "missing_required_params": [],
                "required_params": ["node_id"],
                "accepted_params": ["node_id"],
                "received_params": ["node_id", "parser_type"],
                "example": "get_symbol(node_id='func_12345')",
                "next_step": "Call list_parsers and retry with a canonical parser or registered alias.",
                "parser_type": "bogus_parser",
                "supported_parsers": ["android", "cplus"],
                "supported_aliases": ["cplus", "proc"],
            },
        });
        let (code, message, retryable, details) = error_of(normalize_error(
            ErrorValue::Legacy(&legacy),
            None,
            None,
            None,
            None,
        ));
        assert_eq!(code, "unsupported_parser");
        assert_eq!(message, "Parser 'bogus_parser' is not registered.");
        assert!(!retryable);
        assert_eq!(details, from_value::<Map<String, Value>>(json!({"parser": "bogus_parser"})).unwrap());
    }

    #[test]
    fn exception_paths_use_stable_codes() {
        let (code, message, retryable, details) = error_of(normalize_error(
            ErrorValue::Exception {
                name: "ProjectNotRegisteredError",
                message: Some(
                    "project_id 'nope' is not registered. Known projects: cortext. \
                     Add a project section to .cortext-harness/config/*.json or pass \
                     an explicit override.",
                ),
            },
            None,
            None,
            None,
            None,
        ));
        assert_eq!(code, "project_not_registered");
        assert!(message.starts_with("project_id 'nope' is not registered."));
        assert!(!retryable);
        assert!(details.is_empty());

        let (code, retryable, _) = {
            let envelope = normalize_error(
                ErrorValue::Exception { name: "TimeoutError", message: Some("timed out") },
                None,
                None,
                None,
                None,
            );
            let error = envelope["error"].as_object().unwrap();
            (
                error["code"].as_str().unwrap().to_string(),
                error["retryable"].as_bool().unwrap(),
                (),
            )
        };
        assert_eq!(code, "storage_unavailable");
        assert!(retryable);
    }

    #[test]
    fn result_summary_counts() {
        assert_eq!(
            result_summary(None, true, None),
            "Success. Read structuredContent.data."
        );
        let data = json!({"results": [1, 2, 3]});
        assert_eq!(
            result_summary(Some(&data), true, None),
            "Success: 3 results. Read structuredContent.data."
        );
        let one = json!({"ids": ["a"]});
        assert_eq!(
            result_summary(Some(&one), true, None),
            "Success: 1 ID. Read structuredContent.data."
        );
        assert_eq!(
            result_summary(None, false, Some("boom")),
            "boom"
        );
        assert_eq!(
            result_summary(None, false, None),
            "Tool execution failed. See structuredContent.error."
        );
    }

    #[test]
    fn meta_contract_keys() {
        let meta = result_meta(Some("list_parsers"));
        assert_eq!(
            Value::Object(meta),
            json!({
                "contract": "cortex.mcp.tool-result",
                "contractVersion": "1.0",
                "tool": "list_parsers",
            })
        );
    }
}

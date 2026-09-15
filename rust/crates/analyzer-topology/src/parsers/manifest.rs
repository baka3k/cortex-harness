//! Port `parsers/manifest.py` — safe identity-level adapters cho ecosystem
//! manifests (JSON name extraction + name-pattern regex).

use std::sync::LazyLock;

use regex::Regex;

use crate::models::{confidence, descriptor_type, safe_summary, AnalysisDiagnostic, DescriptorFact, PyValue};
use crate::parsers::common::{evidence, module_path_for_file};
use crate::parsers::DescriptorParseOutput;

static NAME_PATTERN_1: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?m)^\s*(?:name|module|package)\s*[=:]\s*["']?([^"'\s,}]+)"#).expect("regex")
});
static NAME_PATTERN_2: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?m)^\s*module\s+([^\s]+)").expect("regex"));

/// `parse_identity_manifest` — parser/role/depth/flags truyền từ registry spec.
#[allow(clippy::too_many_arguments)]
pub fn parse_identity_manifest(
    project_id: &str,
    path: &str,
    text: &str,
    parser: &str,
    role: &'static str,
    parse_depth_value: &'static str,
    secret_bearing: bool,
    generated: bool,
) -> DescriptorParseOutput {
    let module_path = module_path_for_file(path);
    let mut diagnostics: Vec<AnalysisDiagnostic> = Vec::new();
    let mut properties = PyValue::dict();
    let mut name = String::new();
    if path.to_lowercase().ends_with(".json") {
        match serde_json::from_str::<serde_json::Value>(text) {
            Ok(payload) if payload.is_object() => {
                let object = payload.as_object().expect("checked object");
                // Python: str(payload.get("name") or payload.get("module") or "")
                // — falsy value (""/0/False/None) rơi xuống key kế.
                let candidate = [object.get("name"), object.get("module")]
                    .into_iter()
                    .flatten()
                    .find(|value| json_truthy(value));
                name = candidate
                    .map(|value| match value {
                        serde_json::Value::String(text) => text.clone(),
                        serde_json::Value::Bool(value) => {
                            if *value { "True".into() } else { "False".into() }
                        }
                        other => other.to_string(),
                    })
                    .unwrap_or_default();
                for key in ["version", "type", "private", "workspaces"] {
                    if key == "workspaces" {
                        continue;
                    }
                    if let Some(value) = object.get(key) {
                        // PyValue: bool/int/str đủ cho các key này.
                        let py = match value {
                            serde_json::Value::Bool(value) => PyValue::Bool(*value),
                            serde_json::Value::Number(number) => PyValue::Int(number.as_i64().unwrap_or(0)),
                            serde_json::Value::String(text) => PyValue::Str(text.clone()),
                            other => PyValue::Str(other.to_string()),
                        };
                        properties.set(key, py);
                    }
                }
                if let Some(serde_json::Value::Array(items)) = object.get("workspaces") {
                    let mut declared: Vec<String> = items
                        .iter()
                        .filter_map(|item| item.as_str().map(str::to_string))
                        .collect();
                    declared.sort();
                    declared.dedup();
                    properties.set(
                        "declared_modules",
                        PyValue::List(declared.into_iter().map(PyValue::Str).collect()),
                    );
                }
            }
            Ok(_) => {}
            Err(error) => {
                diagnostics.push(
                    AnalysisDiagnostic::new(
                        crate::models::diagnostic_code::MALFORMED_DESCRIPTOR,
                        &format!("Malformed JSON manifest: {error}"),
                    )
                    .file_path(path)
                    .module_path(&module_path),
                );
            }
        }
    }
    if name.is_empty() {
        for pattern in [&NAME_PATTERN_1, &NAME_PATTERN_2] {
            if let Some(value) = pattern
                .captures(text)
                .and_then(|caps| caps.get(1))
            {
                name = value.as_str().to_string();
                break;
            }
        }
    }
    // properties = {"name": name, **properties} — name TRƯỚC các key khác.
    let mut final_properties = PyValue::dict();
    final_properties.set("name", PyValue::Str(name.clone()));
    if let PyValue::Dict(entries) = properties {
        for (key, value) in entries {
            final_properties.set(&key, value);
        }
    }
    let summary = if secret_bearing {
        "[redacted configuration]".to_string()
    } else {
        safe_summary(&format!(
            "{parser} manifest {}",
            if name.is_empty() { path } else { name.as_str() }
        ))
    };
    let descriptor = DescriptorFact {
        generated,
        canonical: !generated,
        secret_bearing,
        redacted: secret_bearing,
        ..DescriptorFact::create(
            project_id,
            &module_path,
            path,
            descriptor_type::PACKAGE_MANIFEST,
            role,
            parser,
            parse_depth_value,
            summary,
            final_properties,
            if diagnostics.is_empty() { confidence::HIGH } else { confidence::MEDIUM },
            evidence(path),
            diagnostics.clone(),
        )
        .expect("identity manifest descriptor")
    };
    DescriptorParseOutput {
        descriptor,
        dependencies: vec![],
        endpoints: vec![],
        diagnostics,
    }
}

/// Python truthiness cho JSON payload values (name or module or "").
fn json_truthy(value: &serde_json::Value) -> bool {
    match value {
        serde_json::Value::Null => false,
        serde_json::Value::Bool(value) => *value,
        serde_json::Value::Number(number) => number.as_f64().map(|f| f != 0.0).unwrap_or(false),
        serde_json::Value::String(text) => !text.is_empty(),
        serde_json::Value::Array(items) => !items.is_empty(),
        serde_json::Value::Object(map) => !map.is_empty(),
    }
}

//! Port `tools/spring/config.py` — parse application.properties/.yml/.json
//! thành `ConfigValue` + diagnostics.

use std::path::Path;

use serde_json::{Map, Value};

use super::detector::profile_from_config_name;
use super::models::{ConfigValue, Diagnostic, SourceSpan};
use super::yamlmini;
use crate::pyjson::{dumps_compact, py_str};
use crate::pyutil::read_limited;

/// `parse_config_file`.
pub fn parse_config_file(root: &Path, rel_path: &str) -> (Vec<ConfigValue>, Vec<Diagnostic>) {
    let lower = rel_path.to_lowercase();
    let abs_path = root.join(rel_path);
    let profile = profile_from_config_name(rel_path);
    if lower.ends_with(".properties") {
        return parse_properties(&abs_path, rel_path, &profile);
    }
    if lower.ends_with(".yml") || lower.ends_with(".yaml") {
        return parse_yaml(&abs_path, rel_path, &profile);
    }
    if lower.ends_with(".json") {
        return parse_json(&abs_path, rel_path, &profile);
    }
    (Vec::new(), Vec::new())
}

fn parse_properties(abs_path: &Path, rel_path: &str, profile: &str) -> (Vec<ConfigValue>, Vec<Diagnostic>) {
    let text = read_limited(abs_path, 2 * 1024 * 1024);
    let mut values: Vec<ConfigValue> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let mut logical_lines: Vec<(i64, String)> = Vec::new();
    let mut pending = String::new();
    let mut start_line = 1i64;
    for (lineno, raw) in text.split('\n').enumerate() {
        let lineno = (lineno + 1) as i64;
        // Python `rstrip()` (mọi whitespace) rồi splitlines đã tách \r.
        let line = raw.strip_suffix('\r').unwrap_or(raw).trim_end();
        if pending.is_empty() {
            start_line = lineno;
        }
        if line.ends_with('\\') && !line.ends_with("\\\\") {
            pending.push_str(&line[..line.len() - 1]);
            continue;
        }
        pending.push_str(line);
        logical_lines.push((start_line, std::mem::take(&mut pending)));
    }
    if !pending.is_empty() {
        logical_lines.push((start_line, pending));
    }

    for (lineno, line) in logical_lines {
        let stripped = line.trim();
        if stripped.is_empty() || stripped.starts_with('#') || stripped.starts_with('!') {
            continue;
        }
        let (key, sep, value) = split_property(stripped);
        if sep.is_empty() {
            diagnostics.push(Diagnostic::new(
                "spring.config.properties.malformed",
                "Missing key/value separator",
                "warning",
                rel_path,
                lineno,
                lineno,
            ));
            continue;
        }
        values.push(ConfigValue {
            key: unescape_properties(key.trim()),
            value: Value::String(unescape_properties(value.trim())),
            source: SourceSpan::new(rel_path, lineno, lineno),
            profile: profile.to_string(),
            raw_value: value.trim().to_string(),
            resolution_status: "resolved".to_string(),
        });
    }
    (values, diagnostics)
}

/// `_split_property` — key kết thúc tại `=`/`:`/whitespace đầu tiên (escape-aware).
fn split_property(line: &str) -> (String, String, String) {
    let mut escaped = false;
    for (idx, ch) in line.char_indices() {
        if escaped {
            escaped = false;
            continue;
        }
        match ch {
            '\\' => {
                escaped = true;
            }
            '=' | ':' => {
                return (line[..idx].to_string(), ch.to_string(), line[idx + ch.len_utf8()..].to_string());
            }
            c if c.is_whitespace() => {
                return (line[..idx].to_string(), c.to_string(), line[idx + ch.len_utf8()..].to_string());
            }
            _ => {}
        }
    }
    (line.to_string(), String::new(), String::new())
}

fn unescape_properties(value: &str) -> String {
    value
        .replace("\\:", ":")
        .replace("\\=", "=")
        .replace("\\ ", " ")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
}

fn parse_yaml(abs_path: &Path, rel_path: &str, profile: &str) -> (Vec<ConfigValue>, Vec<Diagnostic>) {
    let text = read_limited(abs_path, 2 * 1024 * 1024);
    let mut values: Vec<ConfigValue> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let documents = match yamlmini::load_all(&text) {
        Ok(documents) => documents,
        Err(error) => {
            return (
                Vec::new(),
                vec![Diagnostic::new(
                    "spring.config.yaml.parse_error",
                    &error.0,
                    "error",
                    rel_path,
                    1,
                    1,
                )],
            );
        }
    };
    for (doc_index, document) in documents.iter().enumerate() {
        if document.is_null() {
            continue;
        }
        for (key, value) in flatten_mapping(document, "") {
            values.push(ConfigValue {
                key,
                value: value.clone(),
                source: SourceSpan::new(rel_path, 1, 1),
                profile: profile.to_string(),
                raw_value: if value.is_string() {
                    py_str(&value)
                } else {
                    dumps_compact(&value)
                },
                resolution_status: "resolved".to_string(),
            });
        }
        if !document.is_object() {
            diagnostics.push(Diagnostic::new(
                "spring.config.yaml.non_mapping_document",
                &format!("YAML document {} is not a mapping", doc_index + 1),
                "warning",
                rel_path,
                1,
                1,
            ));
        }
    }
    (values, diagnostics)
}

fn parse_json(abs_path: &Path, rel_path: &str, profile: &str) -> (Vec<ConfigValue>, Vec<Diagnostic>) {
    let text = read_limited(abs_path, 2 * 1024 * 1024);
    let payload: Value = match serde_json::from_str(&text) {
        Ok(payload) => payload,
        Err(error) => {
            return (
                Vec::new(),
                vec![Diagnostic::new(
                    "spring.config.json.parse_error",
                    &error.to_string(),
                    "error",
                    rel_path,
                    1,
                    1,
                )],
            );
        }
    };
    let values = flatten_mapping(&payload, "")
        .into_iter()
        .map(|(key, value)| ConfigValue {
            key,
            value: value.clone(),
            source: SourceSpan::new(rel_path, 1, 1),
            profile: profile.to_string(),
            raw_value: if value.is_string() { py_str(&value) } else { dumps_compact(&value) },
            resolution_status: "resolved".to_string(),
        })
        .collect();
    (values, Vec::new())
}

/// `_flatten_mapping` — leaf scalar yield `prefix`, dict/list đệ quy với key
/// con sorted by str, list theo `[idx]`.
pub fn flatten_mapping(value: &Value, prefix: &str) -> Vec<(String, Value)> {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort_by(|a, b| a.as_str().cmp(b.as_str()));
            let mut out = Vec::new();
            for key in keys {
                let next_key = if prefix.is_empty() {
                    key.clone()
                } else {
                    format!("{prefix}.{key}")
                };
                out.extend(flatten_mapping(&map[key.as_str()], &next_key));
            }
            out
        }
        Value::Array(items) => {
            let mut out = Vec::new();
            for (idx, item) in items.iter().enumerate() {
                let next_key = format!("{prefix}[{idx}]");
                out.extend(flatten_mapping(item, &next_key));
            }
            out
        }
        other => vec![(prefix.to_string(), other.clone())],
    }
}

/// `_config_index` — key → list giá trị (theo thứ tự xuất hiện).
pub fn config_index(values: &[ConfigValue]) -> Map<String, Value> {
    let mut index: Map<String, Value> = Map::new();
    for item in values {
        let entry = index.entry(item.key.clone()).or_insert_with(|| Value::Array(Vec::new()));
        if let Value::Array(items) = entry {
            items.push(item.value.clone());
        }
    }
    index
}

/// Lookup: `config_index.get(key) or []` → danh sách values.
pub fn index_values(index: &Map<String, Value>, key: &str) -> Vec<Value> {
    match index.get(key) {
        Some(Value::Array(items)) => items.clone(),
        Some(other) => vec![other.clone()],
        None => Vec::new(),
    }
}

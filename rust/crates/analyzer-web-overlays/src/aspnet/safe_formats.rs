//! Port `tools/common/aspnet/safe_formats.py` — redact/graph_property_value/
//! read_bounded_text/parse_json_file/parse_xml_file/flatten_json.

use std::path::Path;

use fancy_regex::Regex;
use serde::de::{MapAccess, SeqAccess, Visitor};
use serde::Deserialize;
use serde_json::{Map, Value};

use crate::pyutil::{basename, decode_utf8_replace, realpath};
use crate::xmlmini::{self, Element};

const MAX_LENGTH: usize = 4096;

const SENSITIVE_KEY_PATTERN: &str = r"(?i)(?:pass(?:word|wd)?|secret|token|api[_-]?key|credential|authorization|connectionstring|private[_-]?key|machinekey|cookie|session(?:id)?)";
const CONNECTION_SECRET_PATTERN: &str = r"(?i)(password|pwd|user\s*id|uid|access\s*token)\s*=\s*([^;]+)"; // sensitive-guard:allow (flag name / test sample)
const DOCTYPE_PATTERN: &str = r"(?i)<!\s*(?:DOCTYPE|ENTITY)\b";

fn sensitive_key_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(SENSITIVE_KEY_PATTERN).expect("static regex"))
}

fn connection_secret_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(CONNECTION_SECRET_PATTERN).expect("static regex"))
}

fn doctype_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(DOCTYPE_PATTERN).expect("static regex"))
}

pub fn is_sensitive_key(key: &str) -> bool {
    sensitive_key_regex().is_match(key).unwrap_or(false)
}

/// `redact_value(key, value)` — max_length mặc định 4096.
pub fn redact_value(key: &str, value: &Value) -> Value {
    redact_value_with_limit(key, value, MAX_LENGTH)
}

pub fn redact_value_with_limit(key: &str, value: &Value, max_length: usize) -> Value {
    if is_sensitive_key(key) {
        return Value::String("[REDACTED]".into());
    }
    match value {
        Value::String(text) => {
            let redacted = connection_secret_regex()
                .replace_all(text, |captures: &fancy_regex::Captures| {
                    format!("{}=[REDACTED]", captures.get(1).map(|m| m.as_str()).unwrap_or(""))
                })
                .into_owned();
            if redacted.chars().count() <= max_length {
                Value::String(redacted)
            } else {
                // Python cắt theo code point (str slicing).
                let cut: String = redacted.chars().take(max_length).collect();
                Value::String(format!("{cut}...[TRUNCATED]"))
            }
        }
        Value::Object(map) => {
            let name_fields = ["name", "key", "config_key", "raw_name"];
            let value_fields = ["value", "config_value", "raw_value", "resolved_value"];
            let sensitive_record = map.iter().any(|(item_key, item_value)| {
                name_fields.contains(&item_key.to_lowercase().as_str())
                    && item_value.is_string()
                    && is_sensitive_key(item_value.as_str().unwrap_or(""))
            });
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let mut result = Map::new();
            for nested_key in keys {
                let normalized = nested_key.clone();
                let nested_value = &map[nested_key];
                if sensitive_record
                    && (name_fields.contains(&normalized.to_lowercase().as_str())
                        || value_fields.contains(&normalized.to_lowercase().as_str()))
                {
                    result.insert(normalized, Value::String("[REDACTED]".into()));
                } else {
                    result.insert(normalized, redact_value_with_limit(nested_key, nested_value, max_length));
                }
            }
            Value::Object(result)
        }
        Value::Array(items) => Value::Array(items
            .iter()
            .map(|item| redact_value_with_limit(key, item, max_length))
            .collect()),
        other => other.clone(),
    }
}

/// `graph_property_value` — scalar pass-through; list scalar → list; còn lại →
/// `json.dumps(ensure_ascii=True, sort_keys=True, separators=(",", ":"))`.
pub fn graph_property_value(value: &Value) -> Value {
    match value {
        Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_) => value.clone(),
        Value::Array(rows) => {
            let all_scalar = rows.iter().all(|item| {
                matches!(
                    item,
                    Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_)
                )
            });
            if all_scalar {
                value.clone()
            } else {
                Value::String(crate::pyjson::dumps_compact_sorted_ascii(value))
            }
        }
        Value::Object(_) => Value::String(crate::pyjson::dumps_compact_sorted_ascii(value)),
    }
}

/// `resolve_inside_root` — trả (absolute, relative); lỗi → message khớp
/// ValueError/FileNotFoundError phía Python.
pub fn resolve_inside_root(
    root: &Path,
    path: &str,
    require_exists: bool,
) -> Result<(std::path::PathBuf, String), String> {
    let root_abs = realpath(root);
    let candidate = if Path::new(path).is_absolute() {
        std::path::PathBuf::from(path)
    } else {
        root_abs.join(path)
    };
    let candidate_abs = realpath(&candidate);
    let inside = common_prefix_components(&root_abs, &candidate_abs);
    if !inside || candidate_abs == root_abs {
        return Err(format!("path is outside project root: {path}"));
    }
    if require_exists && !candidate_abs.is_file() {
        return Err(format!(
            "[Errno 2] No such file or directory: '{}'",
            candidate_abs.to_string_lossy()
        ));
    }
    let relative = relative_to(&candidate_abs, &root_abs);
    Ok((candidate_abs, crate::pyutil::normalize_relative_path(&relative)))
}

fn components_of(path: &Path) -> Vec<String> {
    path.components()
        .map(|component| component.as_os_str().to_string_lossy().to_string())
        .collect()
}

/// `os.path.commonpath((root, candidate)) == root` — so sánh theo component.
fn common_prefix_components(root: &Path, candidate: &Path) -> bool {
    let root_parts = components_of(root);
    let candidate_parts = components_of(candidate);
    candidate_parts.len() >= root_parts.len()
        && candidate_parts[..root_parts.len()] == root_parts[..]
}

/// `os.path.relpath(candidate, root)` với candidate nằm dưới root.
fn relative_to(candidate: &Path, root: &Path) -> String {
    let root_parts = components_of(root);
    let candidate_parts = components_of(candidate);
    let tail = &candidate_parts[root_parts.len().min(candidate_parts.len())..];
    tail.join("/")
}

/// `read_bounded_text` — trả (text, relative, truncated); lỗi io/path → Err.
pub fn read_bounded_text(root: &Path, path: &str, max_bytes: usize) -> Result<(String, String, bool), String> {
    let (absolute, relative) = resolve_inside_root(root, path, true)?;
    let payload = std::fs::read(&absolute).map_err(|error| error.to_string())?;
    let truncated = payload.len() > max_bytes;
    let payload = if truncated { &payload[..max_bytes] } else { &payload[..] };
    Ok((decode_utf8_replace(payload), relative, truncated))
}

/// JSON giữ được duplicate keys: deserialize thành list of pairs rồi ép về
/// Map với ngữ nghĩa Python `result[key] = value` (giá trị sau thắng, key giữ
/// vị trí xuất hiện đầu tiên trong preserve_order Map).
#[derive(Debug, Clone)]
enum PyJson {
    Null,
    Bool(bool),
    Number(serde_json::Number),
    String(String),
    Array(Vec<PyJson>),
    Object(Vec<(String, PyJson)>),
}

impl<'de> Deserialize<'de> for PyJson {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        struct PyJsonVisitor;
        impl<'de> Visitor<'de> for PyJsonVisitor {
            type Value = PyJson;
            fn expecting(&self, formatter: &mut std::fmt::Formatter) -> std::fmt::Result {
                formatter.write_str("any JSON value")
            }
            fn visit_bool<E: serde::de::Error>(self, value: bool) -> Result<Self::Value, E> {
                Ok(PyJson::Bool(value))
            }
            fn visit_i64<E: serde::de::Error>(self, value: i64) -> Result<Self::Value, E> {
                Ok(PyJson::Number(value.into()))
            }
            fn visit_u64<E: serde::de::Error>(self, value: u64) -> Result<Self::Value, E> {
                Ok(PyJson::Number(value.into()))
            }
            fn visit_f64<E: serde::de::Error>(self, value: f64) -> Result<Self::Value, E> {
                Ok(PyJson::Number(
                    serde_json::Number::from_f64(value)
                        .ok_or_else(|| E::custom("non-finite float"))?,
                ))
            }
            fn visit_str<E: serde::de::Error>(self, value: &str) -> Result<Self::Value, E> {
                Ok(PyJson::String(value.to_string()))
            }
            fn visit_unit<E: serde::de::Error>(self) -> Result<Self::Value, E> {
                Ok(PyJson::Null)
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut access: A) -> Result<Self::Value, A::Error> {
                let mut items = Vec::new();
                while let Some(item) = access.next_element::<PyJson>()? {
                    items.push(item);
                }
                Ok(PyJson::Array(items))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut access: A) -> Result<Self::Value, A::Error> {
                let mut pairs = Vec::new();
                while let Some(key) = access.next_key::<String>()? {
                    let value = access.next_value::<PyJson>()?;
                    pairs.push((key, value));
                }
                Ok(PyJson::Object(pairs))
            }
        }
        deserializer.deserialize_any(PyJsonVisitor)
    }
}

fn py_json_to_value(py: PyJson, duplicates: &mut Vec<String>) -> Value {
    match py {
        PyJson::Null => Value::Null,
        PyJson::Bool(value) => Value::Bool(value),
        PyJson::Number(value) => Value::Number(value),
        PyJson::String(value) => Value::String(value),
        PyJson::Array(items) => {
            Value::Array(items.into_iter().map(|item| py_json_to_value(item, duplicates)).collect())
        }
        PyJson::Object(pairs) => {
            let mut map = Map::new();
            let mut seen: Vec<String> = Vec::new();
            for (key, value) in pairs {
                if seen.iter().any(|existing| existing == &key) {
                    duplicates.push(key.clone());
                } else {
                    seen.push(key.clone());
                }
                map.insert(key.clone(), py_json_to_value(value, duplicates));
            }
            Value::Object(map)
        }
    }
}

/// Kết quả `parse_json_file`.
pub struct ParsedJson {
    pub value: Value,
    pub relative: String,
    pub truncated: bool,
    pub duplicates: Vec<String>,
}

/// `parse_json_file(root, path, max_bytes)` — duplicate keys: giá trị sau
/// thắng, danh sách duplicate = sorted(set(...)).
pub fn parse_json_file(root: &Path, path: &str, max_bytes: usize) -> Result<ParsedJson, String> {
    let (text, relative, truncated) = read_bounded_text(root, path, max_bytes)?;
    let py: PyJson = serde_json::from_str(&text).map_err(|error| error.to_string())?;
    let mut duplicates: Vec<String> = Vec::new();
    let value = py_json_to_value(py, &mut duplicates);
    duplicates.sort();
    duplicates.dedup();
    Ok(ParsedJson {
        value,
        relative,
        truncated,
        duplicates,
    })
}

/// `flatten_json` — prefix ":"-joined; mapping key sorted; list theo index.
pub fn flatten_json(value: &Value, prefix: &str) -> Map<String, Value> {
    let mut output = Map::new();
    match value {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            for key in keys {
                let child = if prefix.is_empty() {
                    key.clone()
                } else {
                    format!("{prefix}:{key}")
                };
                for (child_key, child_value) in flatten_json(&map[key], &child) {
                    output.insert(child_key, child_value);
                }
            }
        }
        Value::Array(items) => {
            for (index, item) in items.iter().enumerate() {
                let child = if prefix.is_empty() {
                    index.to_string()
                } else {
                    format!("{prefix}:{index}")
                };
                for (child_key, child_value) in flatten_json(item, &child) {
                    output.insert(child_key, child_value);
                }
            }
        }
        other => {
            output.insert(prefix.to_string(), redact_value(prefix, other));
        }
    }
    output
}

/// `parse_xml_file` — DTD/entity guard + parse cây Element.
pub fn parse_xml_file(root: &Path, path: &str, max_bytes: usize) -> Result<(Element, String, bool), String> {
    let (text, relative, truncated) = read_bounded_text(root, path, max_bytes)?;
    if doctype_regex().is_match(&text).unwrap_or(false) {
        return Err(format!("DTD/entity declarations are prohibited: {relative}"));
    }
    let tree = xmlmini::parse_xml(&text)?;
    Ok((tree, relative, truncated))
}

/// `os.path.basename` cho đường dẫn đã posix hoá.
pub fn base_name(path: &str) -> String {
    basename(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_sensitive_keys() {
        assert_eq!(
            redact_value("ConnectionStrings:Default", &Value::String("x".into())),
            Value::String("[REDACTED]".into())
        );
        assert_eq!(
            redact_value("value", &Value::String("Server=a;Password=secret;Uid=b".into())), // sensitive-guard:allow (flag name / test sample)
            Value::String("Server=a;Password=[REDACTED];Uid=[REDACTED]".into()) // sensitive-guard:allow (flag name / test sample)
        );
    }

    #[test]
    fn flatten_json_sorted_keys() {
        let value: Value = serde_json::from_str(r#"{"b": {"y": 1, "x": 2}, "a": [true, null]}"#).unwrap();
        let flat = flatten_json(&value, "");
        let keys: Vec<String> = flat.keys().cloned().collect();
        assert_eq!(keys, vec!["a:0", "a:1", "b:x", "b:y"]);
    }

    #[test]
    fn graph_property_value_dumps_nested_maps() {
        let value = serde_json::from_str::<Value>(r#"{"z": 1, "a": [1, 2]}"#).unwrap();
        let encoded = graph_property_value(&value);
        assert_eq!(encoded, Value::String(r#"{"a":[1,2],"z":1}"#.into()));
    }

    #[test]
    fn json_duplicates_tracked_last_wins() {
        let py: PyJson = serde_json::from_str(r#"{"a": 1, "b": {"k": 1, "k": 2}, "a": 3}"#).unwrap();
        let mut duplicates = Vec::new();
        let value = py_json_to_value(py, &mut duplicates);
        assert_eq!(value["a"], serde_json::json!(3));
        assert_eq!(value["b"]["k"], serde_json::json!(2));
        duplicates.sort();
        duplicates.dedup();
        assert_eq!(duplicates, vec!["a", "k"]);
    }
}

//! Port `tools/spring/extractors/common.py` — helpers dùng chung extractor.

use serde_json::{json, Map, Value};

use crate::spring::models::{SourceSpan, SpringFact, SpringRelationship};
use crate::spring::source_scanner::{SourceAnnotation, SourceClass, SourceMethod};
use crate::pyutil::sha1_hex16;

pub fn stable_hash(text: &str) -> String {
    sha1_hex16(text)
}

/// `fact()` — kwargs thành properties map.
pub struct FactProps(pub Vec<(String, Value)>);

#[allow(clippy::too_many_arguments)]
pub fn fact(
    kind: &str,
    stable_id: &str,
    name: &str,
    source: &SourceSpan,
    project_id: &str,
    project_name: &str,
    language: &str,
    confidence: f64,
    resolution_status: &str,
    raw_value: &str,
    resolved_value: &str,
    source_symbol_id: &str,
    properties: FactProps,
) -> SpringFact {
    let mut props = Map::new();
    for (key, value) in properties.0 {
        props.insert(key, value);
    }
    SpringFact {
        kind: kind.to_string(),
        stable_id: stable_id.to_string(),
        name: name.to_string(),
        source: source.clone(),
        project_id: project_id.to_string(),
        project_name: project_name.to_string(),
        language: if language == "java" || language == "kotlin" {
            format!("spring-{language}")
        } else {
            "spring".to_string()
        },
        confidence,
        extraction_method: "spring_foundation".to_string(),
        resolution_status: resolution_status.to_string(),
        raw_value: raw_value.to_string(),
        resolved_value: resolved_value.to_string(),
        source_symbol_id: source_symbol_id.to_string(),
        properties: props,
    }
}

#[allow(clippy::too_many_arguments)]
pub fn rel(
    rel_type: &str,
    from_label: &str,
    from_id: &str,
    to_label: &str,
    to_id: &str,
    project_id: &str,
    source: &SourceSpan,
    reason: &str,
    confidence: f64,
    resolution_status: &str,
    properties: FactProps,
) -> SpringRelationship {
    let mut props = Map::new();
    for (key, value) in properties.0 {
        props.insert(key, value);
    }
    SpringRelationship {
        rel_type: rel_type.to_string(),
        from_label: from_label.to_string(),
        from_id: from_id.to_string(),
        to_label: to_label.to_string(),
        to_id: to_id.to_string(),
        project_id: project_id.to_string(),
        source: source.clone(),
        reason: reason.to_string(),
        confidence,
        resolution_status: resolution_status.to_string(),
        properties: props,
    }
}

pub fn annotation_map(annotations: &[SourceAnnotation]) -> BTreeMapLike {
    let mut map = BTreeMapLike::default();
    for ann in annotations {
        map.insert(ann.short_name(), ann.clone());
    }
    map
}

#[derive(Default)]
pub struct BTreeMapLike {
    inner: Vec<(String, SourceAnnotation)>,
}

impl BTreeMapLike {
    pub fn insert(&mut self, key: String, value: SourceAnnotation) {
        // dict assignment — ghi đè theo key, giữ thứ tự insert đầu tiên.
        if let Some(slot) = self.inner.iter_mut().find(|(k, _)| *k == key) {
            slot.1 = value;
        } else {
            self.inner.push((key, value));
        }
    }
    pub fn get(&self, key: &str) -> Option<&SourceAnnotation> {
        self.inner.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }
    pub fn contains_key(&self, key: &str) -> bool {
        self.get(key).is_some()
    }
}

pub fn has_annotation(annotations: &[SourceAnnotation], names: &std::collections::BTreeSet<&str>) -> bool {
    annotations.iter().any(|ann| names.contains(&ann.short_name().as_str()))
}

pub fn first_annotation<'a>(
    annotations: &'a [SourceAnnotation],
    names: &std::collections::BTreeSet<&str>,
) -> Option<&'a SourceAnnotation> {
    annotations.iter().find(|ann| names.contains(&ann.short_name().as_str()))
}

/// `bean_name`.
pub fn bean_name(default_name: &str, ann: Option<&SourceAnnotation>) -> String {
    let mut explicit = String::new();
    if let Some(ann) = ann {
        // `ann.args.get("name") or ann.args.get("value")` — truthy-or.
        let name_value = ann.args.get("name").cloned();
        let value = match name_value {
            Some(ref value) if crate::pyjson::py_truthy(value) => value.clone(),
            _ => ann
                .args
                .get("value")
                .cloned()
                .filter(crate::pyjson::py_truthy)
                .unwrap_or(Value::Null),
        };
        if crate::pyjson::py_truthy(&value) {
            match &value {
                Value::Array(items) => {
                    if let Some(first) = items.first() {
                        explicit = crate::pyjson::py_str(first);
                    }
                }
                other => explicit = crate::pyjson::py_str(other),
            }
        }
    }
    if !explicit.is_empty() {
        return explicit;
    }
    if default_name.is_empty() {
        String::new()
    } else {
        format!("{}{}", default_name[..1].to_lowercase(), &default_name[1..])
    }
}

pub fn method_owner_id(method: &SourceMethod) -> String {
    method.symbol_id()
}

pub fn class_owner_id(cls: &SourceClass) -> String {
    cls.symbol_id()
}

/// `parse_extends_types`.
pub fn parse_extends_types(header: &str) -> Vec<String> {
    let text = header;
    let mut out: Vec<String> = Vec::new();
    for marker in ["extends", "implements", ":"] {
        if !text.contains(marker) {
            continue;
        }
        let tail = text.split_once(marker).map(|(_, tail)| tail).unwrap_or("");
        let tail = tail.split('{').next().unwrap_or("");
        for item in split_top_level_commas(tail) {
            if !item.trim().is_empty() {
                out.push(item.trim().to_string());
            }
        }
    }
    out
}

/// `generic_args`.
pub fn generic_args(type_text: &str) -> Vec<String> {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| regex::Regex::new(r"<([^<>]+)>").unwrap());
    let Some(captures) = re.captures(type_text) else {
        return Vec::new();
    };
    let inner = captures.get(1).map(|m| m.as_str()).unwrap_or("");
    inner
        .split(',')
        .map(|item| item.trim().to_string())
        .filter(|item| !item.is_empty())
        .collect()
}

/// `_split_top_level_commas` của extractors/common — KHÔNG quote-aware
/// (chỉ đếm depth `<`), như Python.
pub fn split_top_level_commas(text: &str) -> Vec<String> {
    let mut parts: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut depth = 0i32;
    for ch in text.chars() {
        match ch {
            '<' => depth += 1,
            '>' => depth = (depth - 1).max(0),
            _ => {}
        }
        if ch == ',' && depth == 0 {
            let item = current.trim().to_string();
            if !item.is_empty() {
                parts.push(item);
            }
            current.clear();
            continue;
        }
        current.push(ch);
    }
    let item = current.trim().to_string();
    if !item.is_empty() {
        parts.push(item);
    }
    parts
}

/// Convenience: list property.
pub fn str_list(items: Vec<String>) -> Value {
    Value::Array(items.into_iter().map(|item| json!(item)).collect())
}

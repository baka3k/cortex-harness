//! Port `tools/spring/value_resolver.py` — annotation args parsing, path
//! normalize/combine, placeholder resolve.

use std::collections::BTreeMap;

use regex::Regex;
use serde_json::{Value, Value as Json};

use crate::pyjson::py_str;

pub struct ValueResolver {
    string_re: Regex,
    named_arg_re: Regex,
    placeholder_re: Regex,
}

impl Default for ValueResolver {
    fn default() -> Self {
        Self::new()
    }
}

impl ValueResolver {
    pub fn new() -> Self {
        Self {
            string_re: Regex::new(r#""([^"\\]*(?:\\.[^"\\]*)*)"|'([^'\\]*(?:\\.[^'\\]*)*)'"#).unwrap(),
            named_arg_re: Regex::new(r"([A-Za-z_][\w.-]*)\s*=\s*([^,]+(?:\{[^}]*\})?)").unwrap(),
            placeholder_re: Regex::new(r"\$\{([^}:]+)(?::([^}]+))?\}").unwrap(),
        }
    }

    pub fn strip_quotes(&self, value: &str) -> String {
        let text = value.trim();
        let bytes = text.as_bytes();
        if bytes.len() >= 2 && bytes[0] == bytes[text.len() - 1] && (bytes[0] == b'\'' || bytes[0] == b'"') {
            return text[1..text.len() - 1].to_string();
        }
        text.to_string()
    }

    pub fn extract_string_literals(&self, text: &str) -> Vec<String> {
        let mut values = Vec::new();
        for capture in self.string_re.captures_iter(text) {
            if let Some(group) = capture.get(1) {
                values.push(group.as_str().to_string());
            } else if let Some(group) = capture.get(2) {
                values.push(group.as_str().to_string());
            }
        }
        values
    }

    /// `parse_annotation_args`.
    pub fn parse_annotation_args(&self, raw_args: &str) -> BTreeMap<String, Json> {
        let mut text = raw_args.trim().to_string();
        if text.starts_with('(') && text.ends_with(')') && text.len() >= 2 {
            text = text[1..text.len() - 1].trim().to_string();
        }
        let mut result: BTreeMap<String, Json> = BTreeMap::new();
        if text.is_empty() {
            return result;
        }
        for capture in self.named_arg_re.captures_iter(&text) {
            let key = capture.get(1).unwrap().as_str().to_string();
            let value = capture.get(2).unwrap().as_str().to_string();
            result.insert(key, self.parse_value(&value));
        }
        if result.is_empty() {
            result.insert("value".to_string(), self.parse_value(&text));
        }
        result
    }

    /// `parse_value`.
    pub fn parse_value(&self, value: &str) -> Json {
        let text = value.trim();
        if text.starts_with('{') && text.ends_with('}') && text.len() >= 2 {
            let inner = &text[1..text.len() - 1];
            return Json::Array(
                split_top_level(inner)
                    .iter()
                    .map(|item| self.parse_value(item))
                    .collect(),
            );
        }
        let strings = self.extract_string_literals(text);
        if strings.len() == 1 && (text == format!("\"{}\"", strings[0]) || text == format!("'{}'", strings[0])) {
            return Json::String(strings[0].clone());
        }
        if !strings.is_empty() && text.starts_with('{') {
            return Json::Array(strings.into_iter().map(Json::String).collect());
        }
        let lower = text.to_lowercase();
        if lower == "true" || lower == "false" {
            return Json::Bool(lower == "true");
        }
        Json::String(self.strip_quotes(text))
    }

    /// `first_arg` — giá trị đầu có mặt trong args (không kiểm tra truthy).
    pub fn first_arg(&self, args: &BTreeMap<String, Json>, names: &[&str], default: &str) -> Json {
        for name in names {
            if let Some(value) = args.get(*name) {
                return value.clone();
            }
        }
        Json::String(default.to_string())
    }

    /// `list_arg`.
    pub fn list_arg(&self, args: &BTreeMap<String, Json>, names: &[&str]) -> Vec<String> {
        let value = self.first_arg(args, names, "");
        if value.is_null() || matches!(&value, Json::String(s) if s.is_empty()) {
            return Vec::new();
        }
        match &value {
            Json::Array(items) => items
                .iter()
                .map(py_str)
                .filter(|item| !item.is_empty())
                .collect(),
            other => vec![py_str(other)],
        }
    }

    /// `resolve_placeholders` — trả (resolved, status).
    pub fn resolve_placeholders(&self, value: &str, config_index: &BTreeMap<String, Vec<String>>) -> (String, String) {
        let raw = value.to_string();
        let mut status = "resolved".to_string();
        let replaced = self
            .placeholder_re
            .replace_all(&raw, |captures: &regex::Captures| {
                let key = captures.get(1).map(|m| m.as_str()).unwrap_or("");
                let default = captures.get(2).map(|m| m.as_str());
                let candidates = config_index.get(key).cloned().unwrap_or_default();
                if let Some(last) = candidates.last() {
                    return last.clone();
                }
                if let Some(default) = default {
                    status = "defaulted".to_string();
                    return default.to_string();
                }
                status = "unresolved".to_string();
                captures.get(0).unwrap().as_str().to_string()
            })
            .to_string();
        (replaced, status)
    }

    /// `normalize_path`.
    pub fn normalize_path(&self, path: &str) -> String {
        let mut text = self.strip_quotes(path.trim());
        if text.is_empty() {
            return "/".to_string();
        }
        if !text.starts_with('/') {
            text = format!("/{text}");
        }
        while text.contains("//") {
            text = text.replace("//", "/");
        }
        let trimmed = text.trim_end_matches('/').to_string();
        if trimmed.is_empty() {
            "/".to_string()
        } else {
            trimmed
        }
    }

    /// `combine_paths` — product + sorted unique.
    pub fn combine_paths(&self, prefixes: &[String], suffixes: &[String]) -> Vec<String> {
        let base: Vec<String> = if prefixes.is_empty() {
            vec![String::new()]
        } else {
            prefixes.to_vec()
        };
        let tail: Vec<String> = if suffixes.is_empty() {
            vec![String::new()]
        } else {
            suffixes.to_vec()
        };
        let mut out: Vec<String> = Vec::new();
        for left in &base {
            for right in &tail {
                let joined = format!("{}/{}", left.trim_matches('/'), right.trim_matches('/'));
                let stripped = joined.trim_matches('/').to_string();
                out.push(self.normalize_path(&stripped));
            }
        }
        out.sort();
        out.dedup();
        out
    }
}

/// `_split_top_level` của value_resolver (depth tính `({[<`).
pub fn split_top_level(text: &str) -> Vec<String> {
    let mut items: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut depth = 0i32;
    let mut quote: Option<char> = None;
    let mut escaped = false;
    for ch in text.chars() {
        if escaped {
            current.push(ch);
            escaped = false;
            continue;
        }
        match quote {
            Some(q) => {
                current.push(ch);
                if ch == q {
                    quote = None;
                }
            }
            _ => match ch {
                '\\' => {
                    current.push(ch);
                    escaped = true;
                }
                '\'' | '"' => {
                    quote = Some(ch);
                    current.push(ch);
                }
                '(' | '{' | '[' | '<' => {
                    depth += 1;
                    current.push(ch);
                }
                ')' | '}' | ']' | '>' => {
                    depth = (depth - 1).max(0);
                    current.push(ch);
                }
                ',' if depth == 0 => {
                    let item = current.trim().to_string();
                    if !item.is_empty() {
                        items.push(item);
                    }
                    current.clear();
                }
                _ => current.push(ch),
            },
        }
    }
    let item = current.trim().to_string();
    if !item.is_empty() {
        items.push(item);
    }
    items
}

/// Wrapper tiện dụng — mirror hàm module-level của Python.
pub fn parse_annotation_args(raw_args: &str) -> BTreeMap<String, Value> {
    ValueResolver::new().parse_annotation_args(raw_args)
}

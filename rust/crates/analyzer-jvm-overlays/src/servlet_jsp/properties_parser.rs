//! Port `tools/servlet_jsp/properties_parser.py`.

use regex::Regex;
use serde_json::Value;

use crate::servlet_jsp::models::{Diagnostic, ResourceBudgets, SourceSpan};
use crate::servlet_jsp::path_resolver::{read_bounded_file, resolve_project_path};

#[derive(Debug, Clone)]
pub struct PropertyEntry {
    pub key: String,
    pub value: String,
    pub raw_value: String,
    pub source: SourceSpan,
}

#[derive(Debug, Clone)]
pub struct PropertyReference {
    pub source_key: String,
    pub target_key: String,
    pub default_value: String,
    pub raw: String,
    pub source: SourceSpan,
}

#[derive(Debug, Clone)]
pub struct PropertyTarget {
    pub source_key: String,
    pub raw_value: String,
    pub resolved_path: String,
    pub classification: String,
    pub resolution_status: String,
    pub source: SourceSpan,
}

#[derive(Debug, Clone, Default)]
pub struct PropertiesParseResult {
    pub file_path: String,
    pub entries: Vec<PropertyEntry>,
    pub values: BTree,
    pub resolved_values: BTree,
    pub references: Vec<PropertyReference>,
    pub targets: Vec<PropertyTarget>,
    pub diagnostics: Vec<Diagnostic>,
    pub truncated: bool,
    pub complete: bool,
}

pub type BTree = std::collections::BTreeMap<String, String>;

fn reference_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\$\{([^{}]+)\}").unwrap())
}

fn target_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)(?:^|/)(?:WEB-INF/.*|[^/?#]+\.(?:jsp|jspx|jspf|html?|css|js|mjs|map|png|jpe?g|gif|svg|ico|woff2?|ttf|xml))(?:[?#].*)?$")
            .unwrap()
    })
}

pub fn parse_properties_file(root: &str, file_path: &str, budgets: &ResourceBudgets) -> PropertiesParseResult {
    let resolution = resolve_project_path(root, file_path, "", false, true);
    if resolution.status != "resolved" {
        return PropertiesParseResult {
            file_path: file_path.to_string(),
            diagnostics: vec![Diagnostic::new(
                "servlet_jsp.properties.path_rejected",
                &if resolution.message.is_empty() {
                    format!("Unable to read properties file: {}", resolution.status)
                } else {
                    resolution.message
                },
                "error",
                file_path,
                1,
                1,
            )],
            complete: false,
            ..Default::default()
        };
    }
    let (payload, truncated) = match read_bounded_file(
        std::path::Path::new(&resolution.absolute_path),
        budgets.max_properties_bytes as usize,
    ) {
        Ok(result) => result,
        Err(error) => {
            return PropertiesParseResult {
                file_path: resolution.relative_path.clone(),
                diagnostics: vec![Diagnostic::new(
                    "servlet_jsp.properties.read_error",
                    &error.to_string(),
                    "error",
                    &resolution.relative_path,
                    1,
                    1,
                )],
                complete: false,
                ..Default::default()
            };
        }
    };

    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    if truncated {
        diagnostics.push(Diagnostic::new(
            "servlet_jsp.properties.byte_budget",
            &format!("Properties byte budget {} reached", budgets.max_properties_bytes),
            "warning",
            &resolution.relative_path,
            1,
            1,
        ));
    }
    let text = match String::from_utf8(payload) {
        Ok(text) => text,
        Err(error) => {
            diagnostics.push(Diagnostic::new(
                "servlet_jsp.properties.legacy_encoding",
                "Properties file is not UTF-8; decoded as ISO-8859-1",
                "info",
                &resolution.relative_path,
                1,
                1,
            ));
            // latin-1 decode: byte → char 1:1.
            error.into_bytes().iter().map(|&byte| byte as char).collect()
        }
    };

    let mut entries: Vec<PropertyEntry> = Vec::new();
    let mut values: BTree = BTree::new();
    let mut references: Vec<PropertyReference> = Vec::new();
    for (start_line, end_line, logical) in logical_lines(&text, &mut diagnostics, &resolution.relative_path) {
        let stripped = logical.trim_start_matches([' ', '\t', '\u{c}']).to_string();
        if stripped.is_empty() || stripped.starts_with('#') || stripped.starts_with('!') {
            continue;
        }
        let (raw_key, raw_value) = split_property(&stripped);
        let key = unescape(&raw_key, &mut diagnostics, &resolution.relative_path, start_line, "key");
        let value = unescape(&raw_value, &mut diagnostics, &resolution.relative_path, start_line, "value");
        let source = SourceSpan {
            file_path: resolution.relative_path.clone(),
            start_line,
            end_line,
            start_column: 1,
            end_column: 1,
        };
        if key.is_empty() {
            diagnostics.push(Diagnostic::new(
                "servlet_jsp.properties.empty_key",
                "Properties entry has an empty key",
                "warning",
                &resolution.relative_path,
                start_line,
                end_line,
            ));
            continue;
        }
        if values.contains_key(&key) {
            diagnostics.push(Diagnostic::new(
                "servlet_jsp.properties.duplicate_key",
                &format!("Duplicate property key {key:?}; the last occurrence is effective"),
                "warning",
                &resolution.relative_path,
                start_line,
                end_line,
            ));
        }
        for matched in reference_re().find_iter(&value) {
            let (reference_key, default) = split_reference(&value[matched.start() + 2..matched.end() - 1]);
            references.push(PropertyReference {
                source_key: key.clone(),
                target_key: reference_key,
                default_value: default,
                raw: matched.as_str().to_string(),
                source: source.clone(),
            });
        }
        entries.push(PropertyEntry {
            key: key.clone(),
            value: value.clone(),
            raw_value: raw_value.clone(),
            source,
        });
        values.insert(key, value);
    }

    let (resolved_values, closure_diagnostics) =
        resolve_reference_closure(&values, &entries, &resolution.relative_path, budgets.max_include_depth as usize);
    diagnostics.extend(closure_diagnostics);
    let targets = inventory_targets(root, &resolution.relative_path, &entries, &resolved_values, &mut diagnostics);
    let complete = !truncated && !diagnostics.iter().any(|item| item.severity == "error");
    PropertiesParseResult {
        file_path: resolution.relative_path,
        entries,
        values,
        resolved_values,
        references,
        targets,
        diagnostics,
        truncated,
        complete,
    }
}

fn logical_lines(text: &str, diagnostics: &mut Vec<Diagnostic>, file_path: &str) -> Vec<(i64, i64, String)> {
    let mut logical: Vec<(i64, i64, String)> = Vec::new();
    let mut pending = String::new();
    let mut start_line = 1i64;
    let mut continued = false;
    let lines: Vec<&str> = splitlines(text);
    let total = lines.len();
    for (offset, physical_raw) in lines.iter().enumerate() {
        let line_number = (offset + 1) as i64;
        let mut physical = physical_raw.strip_suffix('\r').unwrap_or(physical_raw).to_string();
        if continued {
            physical = physical.trim_start_matches([' ', '\t', '\u{c}']).to_string();
        } else {
            pending.clear();
            start_line = line_number;
        }
        let slash_count = physical.len() - physical.trim_end_matches('\\').len();
        if slash_count % 2 == 1 {
            pending.push_str(&physical[..physical.len() - 1]);
            continued = true;
            continue;
        }
        pending.push_str(&physical);
        logical.push((start_line, line_number, pending.clone()));
        pending.clear();
        continued = false;
    }
    if continued {
        logical.push((start_line, start_line.max(total as i64), pending));
        diagnostics.push(Diagnostic::new(
            "servlet_jsp.properties.unclosed_continuation",
            "Properties file ends during a continued logical line",
            "warning",
            file_path,
            start_line,
            start_line.max(total as i64),
        ));
    }
    logical
}

fn splitlines(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0usize;
    let bytes = text.as_bytes();
    let mut index = 0usize;
    while index < bytes.len() {
        match bytes[index] {
            b'\n' => {
                out.push(&text[start..index]);
                index += 1;
                start = index;
            }
            b'\r' => {
                out.push(&text[start..index]);
                if index + 1 < bytes.len() && bytes[index + 1] == b'\n' {
                    index += 2;
                } else {
                    index += 1;
                }
                start = index;
            }
            _ => index += 1,
        }
    }
    out.push(&text[start..]);
    out
}

fn split_property(line: &str) -> (String, String) {
    let mut escaped = false;
    let mut key_end = line.len();
    let mut separator: isize = -1;
    for (index, ch) in line.char_indices() {
        if escaped {
            escaped = false;
            continue;
        }
        match ch {
            '\\' => escaped = true,
            '=' | ':' => {
                key_end = index;
                separator = index as isize;
                break;
            }
            c if c.is_whitespace() => {
                key_end = index;
                separator = index as isize;
                break;
            }
            _ => {}
        }
    }
    if separator < 0 {
        return (line.to_string(), String::new());
    }
    let mut cursor = separator as usize;
    let bytes = line.as_bytes();
    while cursor < bytes.len() && bytes[cursor].is_ascii_whitespace() {
        cursor += 1;
    }
    if cursor < bytes.len() && (bytes[cursor] == b'=' || bytes[cursor] == b':') {
        cursor += 1;
    }
    while cursor < bytes.len() && bytes[cursor].is_ascii_whitespace() {
        cursor += 1;
    }
    (line[..key_end].to_string(), line[cursor..].to_string())
}

fn unescape(value: &str, diagnostics: &mut Vec<Diagnostic>, file_path: &str, line: i64, role: &str) -> String {
    let mut output = String::new();
    let chars: Vec<char> = value.chars().collect();
    let mut index = 0usize;
    while index < chars.len() {
        if chars[index] != '\\' {
            output.push(chars[index]);
            index += 1;
            continue;
        }
        index += 1;
        if index >= chars.len() {
            output.push('\\');
            break;
        }
        let marker = chars[index];
        if marker == 'u' {
            let digits: String = chars[index + 1..(index + 5).min(chars.len())].iter().collect();
            if digits.len() == 4 && digits.chars().all(|ch| ch.is_ascii_hexdigit()) {
                if let Ok(code) = u32::from_str_radix(&digits, 16)
                    && let Some(ch) = char::from_u32(code)
                {
                        output.push(ch);
                }
                index += 5;
                continue;
            }
            diagnostics.push(Diagnostic::new(
                "servlet_jsp.properties.invalid_unicode_escape",
                &format!("Invalid Unicode escape in property {role}"),
                "warning",
                file_path,
                line,
                line,
            ));
            output.push_str("\\u");
            index += 1;
            continue;
        }
        output.push(match marker {
            't' => '\t',
            'n' => '\n',
            'r' => '\r',
            'f' => '\u{c}',
            other => other,
        });
        index += 1;
    }
    output
}

fn split_reference(value: &str) -> (String, String) {
    match value.split_once(':') {
        Some((key, default)) => (key.trim().to_string(), default.to_string()),
        None => (value.trim().to_string(), String::new()),
    }
}

fn resolve_reference_closure(
    values: &BTree,
    entries: &[PropertyEntry],
    file_path: &str,
    max_depth: usize,
) -> (BTree, Vec<Diagnostic>) {
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let sources: BTreeMap = entries
        .iter()
        .map(|item| (item.key.clone(), item.source.clone()))
        .collect();
    let mut cache: BTree = BTree::new();
    let mut reported_cycles: std::collections::BTreeSet<Vec<String>> = std::collections::BTreeSet::new();

    #[allow(clippy::too_many_arguments)]
    fn resolve(
        key: &str,
        stack: &[String],
        values: &BTree,
        sources: &std::collections::BTreeMap<String, SourceSpan>,
        cache: &mut BTree,
        reported_cycles: &mut std::collections::BTreeSet<Vec<String>>,
        diagnostics: &mut Vec<Diagnostic>,
        file_path: &str,
        max_depth: usize,
    ) -> String {
        if let Some(cached) = cache.get(key) {
            return cached.clone();
        }
        if stack.iter().any(|item| item == key) {
            let index = stack.iter().position(|item| item == key).unwrap_or(0);
            let mut cycle: Vec<String> = stack[index..].to_vec();
            cycle.push(key.to_string());
            let mut canonical = {
                let unique: std::collections::BTreeSet<String> = cycle.iter().cloned().collect();
                unique.into_iter().collect::<Vec<String>>()
            };
            canonical.sort();
            if !reported_cycles.contains(&canonical) {
                reported_cycles.insert(canonical);
                let source = sources
                    .get(key)
                    .cloned()
                    .unwrap_or_else(|| SourceSpan::new(file_path));
                diagnostics.push(Diagnostic::new(
                    "servlet_jsp.properties.reference_cycle",
                    &format!("Property reference cycle: {}", cycle.join(" -> ")),
                    "warning",
                    file_path,
                    source.start_line,
                    source.end_line,
                ));
            }
            return values.get(key).cloned().unwrap_or_default();
        }
        if stack.len() >= max_depth {
            let source = sources
                .get(key)
                .cloned()
                .unwrap_or_else(|| SourceSpan::new(file_path));
            diagnostics.push(Diagnostic::new(
                "servlet_jsp.properties.reference_depth",
                &format!("Property reference depth budget {max_depth} reached"),
                "warning",
                file_path,
                source.start_line,
                source.end_line,
            ));
            return values.get(key).cloned().unwrap_or_default();
        }
        let raw = values.get(key).cloned().unwrap_or_default();
        let mut stack = stack.to_vec();
        stack.push(key.to_string());
        let resolved = reference_re()
            .replace_all(&raw, |captures: &regex::Captures| {
                let (target, default) = split_reference(&captures[1]);
                if values.contains_key(&target) {
                    resolve(
                        &target,
                        &stack,
                        values,
                        sources,
                        cache,
                        reported_cycles,
                        diagnostics,
                        file_path,
                        max_depth,
                    )
                } else if !default.is_empty() {
                    default
                } else {
                    captures.get(0).unwrap().as_str().to_string()
                }
            })
            .to_string();
        cache.insert(key.to_string(), resolved.clone());
        resolved
    }

    let keys: Vec<String> = values.keys().cloned().collect();
    for key in keys {
        resolve(
            &key,
            &Vec::new(),
            values,
            &sources,
            &mut cache,
            &mut reported_cycles,
            &mut diagnostics,
            file_path,
            max_depth,
        );
    }
    (cache, diagnostics)
}

type BTreeMap = std::collections::BTreeMap<String, SourceSpan>;

fn inventory_targets(
    root: &str,
    file_path: &str,
    entries: &[PropertyEntry],
    resolved_values: &BTree,
    diagnostics: &mut Vec<Diagnostic>,
) -> Vec<PropertyTarget> {
    let mut targets: Vec<PropertyTarget> = Vec::new();
    for entry in entries {
        let value = resolved_values
            .get(&entry.key)
            .cloned()
            .unwrap_or_else(|| entry.value.clone())
            .trim()
            .to_string();
        let is_target = target_re().is_match(&value)
            || value.starts_with("http://")
            || value.starts_with("https://");
        if value.is_empty() || !is_target {
            continue;
        }
        if reference_re().is_match(&value) {
            targets.push(PropertyTarget {
                source_key: entry.key.clone(),
                raw_value: value,
                resolved_path: String::new(),
                classification: "dynamic".to_string(),
                resolution_status: "dynamic".to_string(),
                source: entry.source.clone(),
            });
            continue;
        }
        let mut classification = if value.starts_with('/') {
            "context_relative".to_string()
        } else {
            "relative".to_string()
        };
        let resolution = resolve_project_path(root, &value, file_path, value.starts_with('/'), false);
        let status = resolution.status.clone();
        match status.as_str() {
            "external" => classification = "external".to_string(),
            "rejected" | "invalid" => {
                classification = "rejected".to_string();
                diagnostics.push(Diagnostic::new(
                    "servlet_jsp.properties.target_rejected",
                    &resolution.message,
                    "warning",
                    file_path,
                    entry.source.start_line,
                    entry.source.end_line,
                ));
            }
            _ => {}
        }
        targets.push(PropertyTarget {
            source_key: entry.key.clone(),
            raw_value: value,
            resolved_path: resolution.relative_path,
            classification,
            resolution_status: status,
            source: entry.source.clone(),
        });
    }
    targets
}

/// JSON accessor giữ cho sink cảnh báo unused khi thiếu feature.
#[allow(dead_code)]
fn unused(_: &Value) {}

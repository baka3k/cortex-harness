//! Port `tools/servlet_jsp/jsp_parser.py` — JSP region scanner + EL/tag/
//! scriptlet extraction + target/dependency inventory.

use std::collections::BTreeMap;

use regex::Regex;
use serde_json::Value;

use crate::servlet_jsp::el_parser::{parse_el_expression, ElParseResult};
use crate::servlet_jsp::models::{Diagnostic, ResourceBudgets, SourceSpan};
use crate::servlet_jsp::path_resolver::{read_bounded_file, resolve_project_path};

fn tag_name_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[A-Za-z_][A-Za-z0-9_.:-]*").unwrap())
}
const JSTL_CORE_URIS: [&str; 3] = [
    "http://java.sun.com/jsp/jstl/core",
    "http://xmlns.jcp.org/jsp/jstl/core",
    "jakarta.tags.core",
];
const JSTL_FORMAT_URIS: [&str; 3] = [
    "http://java.sun.com/jsp/jstl/fmt",
    "http://xmlns.jcp.org/jsp/jstl/fmt",
    "jakarta.tags.fmt",
];
const JSTL_FUNCTION_URIS: [&str; 3] = [
    "http://java.sun.com/jsp/jstl/functions",
    "http://xmlns.jcp.org/jsp/jstl/functions",
    "jakarta.tags.functions",
];
const JSP_XML_URIS: [&str; 3] = [
    "http://java.sun.com/JSP/Page",
    "http://xmlns.jcp.org/JSP/Page",
    "jakarta.tags.jsp",
];

fn resource_attribute(local: &str) -> Option<&'static str> {
    match local {
        "img" | "script" | "iframe" | "source" | "video" | "audio" | "input" => Some("src"),
        "link" => Some("href"),
        _ => None,
    }
}

#[derive(Debug, Clone, Default)]
pub struct JspRegion {
    pub kind: String,
    pub name: String,
    pub raw: String,
    pub attributes: BTreeMap<String, String>,
    pub span: SourceSpan,
    pub self_closing: bool,
    pub malformed: bool,
    pub taglib_uri: String,
    pub semantic_kind: String,
}

#[derive(Debug, Clone, Default)]
pub struct JspExpression {
    pub kind: String,
    pub raw: String,
    pub span: SourceSpan,
    pub el: Option<ElParseResult>,
}

impl JspExpression {
    pub fn functions(&self) -> &[crate::servlet_jsp::el_parser::ElFunctionReference] {
        match &self.el {
            Some(el) => &el.functions,
            None => &[],
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct JspTarget {
    pub kind: String,
    pub raw_value: String,
    pub resolved_path: String,
    pub classification: String,
    pub resolution_status: String,
    pub span: SourceSpan,
    pub method: String,
    pub source_name: String,
    pub dynamic: bool,
}

#[derive(Debug, Clone, Default)]
pub struct JspDependency {
    pub source_path: String,
    pub target_path: String,
    pub kind: String,
    pub dynamic: bool,
    pub resolution_status: String,
    pub span: SourceSpan,
    pub raw_target: String,
}

#[derive(Debug, Clone, Default)]
pub struct JspScriptletOperation {
    pub kind: String,
    pub scope: String,
    pub name: String,
    pub raw: String,
    pub resolution_status: String,
    pub span: SourceSpan,
}

#[derive(Debug, Clone, Default)]
pub struct JspParseResult {
    pub file_path: String,
    pub syntax: String,
    pub regions: Vec<JspRegion>,
    pub directives: Vec<JspRegion>,
    pub actions: Vec<JspRegion>,
    pub tags: Vec<JspRegion>,
    pub expressions: Vec<JspExpression>,
    pub scriptlet_operations: Vec<JspScriptletOperation>,
    pub taglibs: BTreeMap<String, String>,
    pub targets: Vec<JspTarget>,
    pub dependencies: Vec<JspDependency>,
    pub diagnostics: Vec<Diagnostic>,
    pub truncated: bool,
    pub complete: bool,
}

struct LineMap {
    file_path: String,
    starts: Vec<usize>,
}

impl LineMap {
    fn new(text: &str, file_path: &str) -> Self {
        let mut starts = vec![0usize];
        for (index, ch) in text.char_indices() {
            if ch == '\n' {
                starts.push(index + 1);
            }
        }
        Self {
            file_path: file_path.to_string(),
            starts,
        }
    }

    /// `span(start, end)` — offsets là CHAR index (Python str index).
    fn span(&self, text: &str, start: usize, end: usize) -> SourceSpan {
        let start_byte = char_to_byte(text, start);
        let end_position = start.max(end.saturating_sub(1));
        let end_byte = char_to_byte(text, end_position);
        let start_line_index = bisect_right(&self.starts, start_byte) - 1;
        let end_line_index = bisect_right(&self.starts, end_byte) - 1;
        SourceSpan {
            file_path: self.file_path.clone(),
            start_line: start_line_index as i64 + 1,
            end_line: end_line_index as i64 + 1,
            start_column: (start - self.starts[start_line_index]) as i64 + 1,
            end_column: (end_position - self.starts[end_line_index]) as i64 + 2,
        }
    }
}

fn char_to_byte(text: &str, char_index: usize) -> usize {
    text.char_indices()
        .nth(char_index)
        .map(|(byte, _)| byte)
        .unwrap_or(text.len())
}

fn bisect_right(starts: &[usize], value: usize) -> usize {
    let mut low = 0usize;
    let mut high = starts.len();
    while low < high {
        let mid = (low + high) / 2;
        if starts[mid] <= value {
            low = mid + 1;
        } else {
            high = mid;
        }
    }
    low
}

pub fn parse_jsp_file(root: &str, file_path: &str, budgets: &ResourceBudgets) -> JspParseResult {
    let resolution = resolve_project_path(root, file_path, "", false, true);
    if resolution.status != "resolved" {
        return failed_result(
            file_path,
            "servlet_jsp.jsp.path_rejected",
            &if resolution.message.is_empty() {
                format!("Unable to read JSP file: {}", resolution.status)
            } else {
                resolution.message
            },
        );
    }
    let (payload, byte_truncated) =
        match read_bounded_file(std::path::Path::new(&resolution.absolute_path), budgets.max_source_bytes as usize) {
            Ok(result) => result,
            Err(error) => {
                return failed_result(&resolution.relative_path, "servlet_jsp.jsp.read_error", &error.to_string());
            }
        };
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    if byte_truncated {
        diagnostics.push(Diagnostic::new(
            "servlet_jsp.jsp.byte_budget",
            &format!("JSP byte budget {} reached", budgets.max_source_bytes),
            "warning",
            &resolution.relative_path,
            1,
            1,
        ));
    }
    // utf-8-sig decode (bỏ BOM), fallback latin-1.
    const UTF8_BOM: &[u8] = &[0xEF, 0xBB, 0xBF];
    let payload_body: &[u8] = payload.strip_prefix(UTF8_BOM).unwrap_or(&payload);
    let text = match String::from_utf8(payload_body.to_vec())
    {
        Ok(text) => text,
        Err(error) => {
            diagnostics.push(Diagnostic::new(
                "servlet_jsp.jsp.legacy_encoding",
                "JSP is not UTF-8; decoded as ISO-8859-1",
                "info",
                &resolution.relative_path,
                1,
                1,
            ));
            error.into_bytes().iter().map(|&byte| byte as char).collect()
        }
    };
    let syntax = if resolution.relative_path.to_lowercase().ends_with(".jspx") {
        "jspx"
    } else {
        "classic"
    };
    if syntax == "jspx" {
        // XML capability check cho jspx: diagnostic khi parser lỗi đã bị bỏ —
        // tree-sitter xml luôn khả dụng phía Rust.
        let _ = &payload;
    }
    let (mut regions, mut expressions, scan_diagnostics, region_truncated) =
        scan_regions(&text, &resolution.relative_path, budgets);
    diagnostics.extend(scan_diagnostics);
    let (normalized, taglibs) = normalize_regions(&mut regions, syntax);
    let known_expression_spans: std::collections::BTreeSet<(i64, i64, String)> = expressions
        .iter()
        .map(|item| (item.span.start_line, item.span.start_column, item.kind.clone()))
        .collect();
    for region in &normalized {
        let expression_kind = if region.kind == "expression" {
            "jsp_expression"
        } else {
            ""
        };
        let key = (region.span.start_line, region.span.start_column, expression_kind.to_string());
        if !expression_kind.is_empty() && !known_expression_spans.contains(&key) {
            expressions.push(JspExpression {
                kind: expression_kind.to_string(),
                raw: region.raw.clone(),
                span: region.span.clone(),
                el: None,
            });
        }
    }
    let (mut targets, dependencies) = extract_targets(root, &resolution.relative_path, &normalized);
    let (scriptlet_operations, scriptlet_targets) =
        extract_scriptlet_operations(root, &resolution.relative_path, &normalized);
    targets.extend(scriptlet_targets);
    let directives: Vec<JspRegion> = normalized
        .iter()
        .filter(|item| item.kind == "directive")
        .cloned()
        .collect();
    let actions: Vec<JspRegion> = normalized
        .iter()
        .filter(|item| item.kind == "start_tag" && !item.semantic_kind.is_empty())
        .cloned()
        .collect();
    let tags: Vec<JspRegion> = normalized
        .iter()
        .filter(|item| item.kind == "start_tag")
        .cloned()
        .collect();
    let truncated = byte_truncated
        || region_truncated
        || expressions.iter().any(|expression| {
            expression
                .el
                .as_ref()
                .map(|el| el.truncated)
                .unwrap_or(false)
        });
    for expression in &expressions {
        if let Some(el) = &expression.el {
            diagnostics.extend(el.diagnostics.iter().cloned());
        }
    }
    diagnostics.truncate(budgets.max_diagnostics_per_file as usize);
    let complete = !truncated && !diagnostics.iter().any(|item| item.severity == "error");
    JspParseResult {
        file_path: resolution.relative_path,
        syntax: syntax.to_string(),
        regions: normalized,
        directives,
        actions,
        tags,
        expressions,
        scriptlet_operations,
        taglibs,
        targets,
        dependencies,
        diagnostics,
        truncated,
        complete,
    }
}

fn failed_result(file_path: &str, code: &str, message: &str) -> JspParseResult {
    JspParseResult {
        file_path: file_path.to_string(),
        syntax: if file_path.to_lowercase().ends_with(".jspx") {
            "jspx".to_string()
        } else {
            "classic".to_string()
        },
        diagnostics: vec![Diagnostic::new(code, message, "error", file_path, 1, 1)],
        complete: false,
        ..Default::default()
    }
}

/// Byte offsets của tất cả char boundaries — scan dùng char index như Python.
struct CharText {
    chars: Vec<char>,
    text: String,
}

impl CharText {
    fn new(text: &str) -> Self {
        Self {
            chars: text.chars().collect(),
            text: text.to_string(),
        }
    }

    #[allow(clippy::needless_range_loop)]
    #[allow(clippy::explicit_counter_loop)]
    fn find_str(&self, needle: &str, from: usize) -> isize {
        let needle_chars: Vec<char> = needle.chars().collect();
        let mut index = from;
        while index < self.chars.len() {
            let window = &self.chars[index..(index + needle_chars.len()).min(self.chars.len())];
            if window == needle_chars.as_slice() {
                return index as isize;
            }
            index += 1;
        }
        -1
    }

    fn slice(&self, start: usize, end: usize) -> String {
        self.chars[start.min(self.chars.len())..end.min(self.chars.len())].iter().collect()
    }

    fn starts_with(&self, needle: &str, at: usize) -> bool {
        let needle_chars: Vec<char> = needle.chars().collect();
        at + needle_chars.len() <= self.chars.len()
            && self.chars[at..at + needle_chars.len()] == needle_chars[..]
    }
}

#[allow(clippy::too_many_arguments)]
fn add_region(
    region: JspRegion,
    regions: &mut Vec<JspRegion>,
    truncated: &mut bool,
    diagnostics: &mut Vec<Diagnostic>,
    budgets: &ResourceBudgets,
    file_path: &str,
) -> bool {
    if regions.len() >= budgets.max_jsp_regions as usize {
        let already = *truncated
            || diagnostics
                .iter()
                .any(|item| item.code == "servlet_jsp.jsp.region_budget");
        if !already {
            diagnostics.push(Diagnostic::new(
                "servlet_jsp.jsp.region_budget",
                &format!("JSP region budget {} reached", budgets.max_jsp_regions),
                "warning",
                file_path,
                region.span.start_line,
                region.span.end_line,
            ));
        }
        *truncated = true;
        return false;
    }
    regions.push(region);
    true
}

#[allow(clippy::too_many_arguments)]
fn flush_template(
    raw: &CharText,
    template_start: usize,
    end: usize,
    line_map: &LineMap,
    regions: &mut Vec<JspRegion>,
    truncated: &mut bool,
    diagnostics: &mut Vec<Diagnostic>,
    budgets: &ResourceBudgets,
    file_path: &str,
) -> bool {
    if end <= template_start {
        return true;
    }
    let region = JspRegion {
        kind: "template".into(),
        raw: raw.slice(template_start, end),
        span: line_map.span(&raw.text, template_start, end),
        ..Default::default()
    };
    add_region(region, regions, truncated, diagnostics, budgets, file_path)
}

fn scan_regions(
    text: &str,
    file_path: &str,
    budgets: &ResourceBudgets,
) -> (Vec<JspRegion>, Vec<JspExpression>, Vec<Diagnostic>, bool) {
    let raw = CharText::new(text);
    let mut regions: Vec<JspRegion> = Vec::new();
    let mut expressions: Vec<JspExpression> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let line_map = LineMap::new(text, file_path);
    let mut cursor = 0usize;
    let mut template_start = 0usize;
    let mut truncated = false;

    while cursor < raw.chars.len() {
        let next_cursor = next_special(&raw, cursor);
        if next_cursor < 0 {
            break;
        }
        cursor = next_cursor as usize;
        if !flush_template(
            &raw, template_start, cursor, &line_map, &mut regions, &mut truncated, &mut diagnostics, budgets, file_path,
        ) {
            break;
        }
        let end;
        if raw.starts_with("<%--", cursor) {
            let close = raw.find_str("--%>", cursor + 5);
            end = if close < 0 { raw.chars.len() } else { (close as usize) + 4 };
            let malformed = close < 0;
            if malformed {
                diagnostics.push(malformed_diagnostic(file_path, line_map.span(text, cursor, end), "JSP comment"));
            }
            let region = JspRegion {
                kind: "comment".into(),
                raw: raw.slice(cursor, end),
                span: line_map.span(text, cursor, end),
                malformed,
                ..Default::default()
            };
            if !add_region(region, &mut regions, &mut truncated, &mut diagnostics, budgets, file_path) {
                break;
            }
        } else if raw.starts_with("<%", cursor) {
            let (marker_length, kind) = jsp_region_kind(&raw, cursor);
            let close = find_jsp_close(&raw, cursor + marker_length);
            let malformed = close < 0;
            end = if malformed {
                recovery_boundary(&raw, cursor + marker_length)
            } else {
                (close as usize) + 2
            };
            if malformed {
                diagnostics.push(malformed_diagnostic(file_path, line_map.span(text, cursor, end), &kind));
            }
            let region_raw = raw.slice(cursor, end);
            let (mut name, mut attributes) = (String::new(), BTreeMap::new());
            if kind == "directive" {
                let parsed = parse_directive(&region_raw);
                name = parsed.0;
                attributes = parsed.1;
            }
            let region = JspRegion {
                kind: kind.clone(),
                name,
                raw: region_raw.clone(),
                attributes,
                span: line_map.span(text, cursor, end),
                malformed,
                ..Default::default()
            };
            if !add_region(region, &mut regions, &mut truncated, &mut diagnostics, budgets, file_path) {
                break;
            }
            if kind == "expression" {
                expressions.push(JspExpression {
                    kind: "jsp_expression".into(),
                    raw: region_raw,
                    span: line_map.span(text, cursor, end),
                    el: None,
                });
            }
        } else if raw.starts_with("<!--", cursor) {
            let close = raw.find_str("-->", cursor + 4);
            end = if close < 0 { raw.chars.len() } else { (close as usize) + 3 };
            let malformed = close < 0;
            if malformed {
                diagnostics.push(malformed_diagnostic(file_path, line_map.span(text, cursor, end), "HTML comment"));
            }
            let region = JspRegion {
                kind: "comment".into(),
                raw: raw.slice(cursor, end),
                span: line_map.span(text, cursor, end),
                malformed,
                ..Default::default()
            };
            if !add_region(region, &mut regions, &mut truncated, &mut diagnostics, budgets, file_path) {
                break;
            }
        } else if raw.starts_with("${", cursor) || raw.starts_with("#{", cursor) {
            let close = find_el_close(&raw, cursor + 2);
            let malformed = close < 0;
            end = if malformed {
                recovery_boundary(&raw, cursor + 2)
            } else {
                (close as usize) + 1
            };
            let region_raw = raw.slice(cursor, end);
            let span = line_map.span(text, cursor, end);
            let el = parse_el_expression(&region_raw, file_path, span.start_line, span.start_column, budgets);
            let region = JspRegion {
                kind: "el".into(),
                raw: region_raw,
                span: line_map.span(text, cursor, end),
                malformed,
                ..Default::default()
            };
            if !add_region(region, &mut regions, &mut truncated, &mut diagnostics, budgets, file_path) {
                break;
            }
            expressions.push(JspExpression {
                kind: "el".into(),
                raw: raw.slice(cursor, end),
                span: line_map.span(text, cursor, end),
                el: Some(el),
            });
        } else if raw.chars[cursor] == '<' && looks_like_tag(&raw, cursor) {
            let close = find_tag_close(&raw, cursor + 1);
            let malformed = close < 0;
            end = if malformed {
                recovery_boundary(&raw, cursor + 1)
            } else {
                (close as usize) + 1
            };
            let region_raw = raw.slice(cursor, end);
            let (kind, name, attributes, self_closing) = parse_tag(&region_raw);
            if malformed {
                diagnostics.push(malformed_diagnostic(file_path, line_map.span(text, cursor, end), "tag"));
            }
            let region = JspRegion {
                kind: kind.clone(),
                name: name.clone(),
                raw: region_raw.clone(),
                attributes,
                span: line_map.span(text, cursor, end),
                self_closing,
                malformed,
                ..Default::default()
            };
            if !add_region(region, &mut regions, &mut truncated, &mut diagnostics, budgets, file_path) {
                break;
            }
            if kind == "start_tag" {
                let found = expressions_in_region(&region_raw, cursor, &line_map, text, budgets);
                expressions.extend(found);
            }
        } else {
            cursor += 1;
            template_start = template_start.min(cursor);
            continue;
        }
        cursor = end;
        template_start = end;
        if diagnostics.len() >= budgets.max_diagnostics_per_file as usize {
            truncated = true;
            break;
        }
    }
    if !truncated {
        flush_template(
            &raw,
            template_start,
            raw.chars.len(),
            &line_map,
            &mut regions,
            &mut truncated,
            &mut diagnostics,
            budgets,
            file_path,
        );
    }
    (regions, expressions, diagnostics, truncated)
}

fn next_special(text: &CharText, start: usize) -> isize {
    let candidates: Vec<isize> = [
        text.find_str("<", start),
        text.find_str("${", start),
        text.find_str("#{", start),
    ]
    .into_iter()
    .filter(|value| *value >= 0)
    .collect();
    if candidates.is_empty() {
        -1
    } else {
        candidates.into_iter().min().unwrap()
    }
}

fn jsp_region_kind(text: &CharText, start: usize) -> (usize, String) {
    if text.starts_with("<%@", start) {
        return (3, "directive".to_string());
    }
    if text.starts_with("<%!", start) {
        return (3, "declaration".to_string());
    }
    if text.starts_with("<%=", start) {
        return (3, "expression".to_string());
    }
    (2, "scriptlet".to_string())
}

fn find_jsp_close(text: &CharText, start: usize) -> isize {
    let mut quote = ' ';
    let mut escaped = false;
    let mut line_comment = false;
    let mut block_comment = false;
    let mut index = start;
    while index + 1 < text.chars.len() {
        let pair: String = text.chars[index..index + 2].iter().collect();
        let ch = text.chars[index];
        if line_comment {
            if ch == '\r' || ch == '\n' {
                line_comment = false;
            }
            index += 1;
            continue;
        }
        if block_comment {
            if pair == "*/" {
                block_comment = false;
                index += 2;
            } else {
                index += 1;
            }
            continue;
        }
        if quote != ' ' {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == quote {
                quote = ' ';
            }
            index += 1;
            continue;
        }
        if pair == "%>" {
            return index as isize;
        }
        if pair == "//" {
            line_comment = true;
            index += 2;
            continue;
        }
        if pair == "/*" {
            block_comment = true;
            index += 2;
            continue;
        }
        if ch == '\'' || ch == '"' {
            quote = ch;
        }
        index += 1;
    }
    -1
}

fn find_el_close(text: &CharText, start: usize) -> isize {
    let mut depth = 1i32;
    let mut quote = ' ';
    let mut escaped = false;
    let mut index = start;
    while index < text.chars.len() {
        let ch = text.chars[index];
        if quote != ' ' {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == quote {
                quote = ' ';
            }
        } else if ch == '\'' || ch == '"' {
            quote = ch;
        } else if ch == '{' {
            depth += 1;
        } else if ch == '}' {
            depth -= 1;
            if depth == 0 {
                return index as isize;
            }
        }
        index += 1;
    }
    -1
}

fn find_tag_close(text: &CharText, start: usize) -> isize {
    let mut quote = ' ';
    let mut escaped = false;
    for index in start..text.chars.len() {
        let ch = text.chars[index];
        if quote != ' ' {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == quote {
                quote = ' ';
            }
        } else if ch == '\'' || ch == '"' {
            quote = ch;
        } else if ch == '>' {
            return index as isize;
        }
    }
    -1
}

fn recovery_boundary(text: &CharText, start: usize) -> usize {
    let newline = text.find_str("\n", start);
    let search_start = if newline >= 0 { newline as usize + 1 } else { start };
    let candidates: Vec<isize> = [
        text.find_str("<", search_start),
        text.find_str("${", search_start),
        text.find_str("#{", search_start),
    ]
    .into_iter()
    .filter(|value| *value >= 0)
    .collect();
    if candidates.is_empty() {
        text.chars.len()
    } else {
        candidates.into_iter().min().unwrap() as usize
    }
}

fn looks_like_tag(text: &CharText, start: usize) -> bool {
    if start + 1 >= text.chars.len() {
        return false;
    }
    let ch = text.chars[start + 1];
    ch.is_alphabetic() || ch == '/' || ch == '!' || ch == '?' || ch == '_'
}

fn match_tag_name_at(chars: &[char], cursor: usize) -> Option<(String, usize)> {
    if cursor >= chars.len() {
        return None;
    }
    let first = chars[cursor];
    if !(first.is_alphabetic() || first == '_') {
        return None;
    }
    let mut end = cursor + 1;
    while end < chars.len()
        && (chars[end].is_alphanumeric() || chars[end] == '_' || chars[end] == '.' || chars[end] == ':' || chars[end] == '-')
    {
        end += 1;
    }
    Some((chars[cursor..end].iter().collect(), end))
}

fn match_attribute_name_at(chars: &[char], cursor: usize) -> Option<(String, usize)> {
    if cursor >= chars.len() {
        return None;
    }
    let first = chars[cursor];
    if !(first.is_alphabetic() || first == '_' || first == ':') {
        return None;
    }
    let mut end = cursor + 1;
    while end < chars.len()
        && (chars[end].is_alphanumeric() || chars[end] == '_' || chars[end] == ':' || chars[end] == '.' || chars[end] == '-')
    {
        end += 1;
    }
    Some((chars[cursor..end].iter().collect(), end))
}

fn parse_directive(raw: &str) -> (String, BTreeMap<String, String>) {
    let content = if raw.ends_with("%>") {
        &raw[3..raw.len() - 2]
    } else {
        &raw[3..]
    };
    // re.search — match đầu tiên bất kỳ đâu.
    let Some(matched) = tag_name_re().find(content) else {
        return (String::new(), BTreeMap::new());
    };
    (matched.as_str().to_string(), parse_attributes(content, matched.end()))
}

fn parse_tag(raw: &str) -> (String, String, BTreeMap<String, String>, bool) {
    let content = if raw.ends_with('>') {
        &raw[1..raw.len() - 1]
    } else {
        &raw[1..]
    };
    let stripped = content.trim_start();
    if stripped.starts_with('!') || stripped.starts_with('?') {
        return (
            "declaration".to_string(),
            String::new(),
            BTreeMap::new(),
            stripped.trim_end().ends_with('/'),
        );
    }
    let is_end = stripped.starts_with('/');
    let stripped = if is_end {
        stripped[1..].trim_start()
    } else {
        stripped
    };
    // _TAG_NAME_RE.match(stripped) — anchor tại vị trí 0.
    let chars: Vec<char> = stripped.chars().collect();
    let Some((name, end)) = match_tag_name_at(&chars, 0) else {
        return ("template".to_string(), String::new(), BTreeMap::new(), false);
    };
    (
        if is_end { "end_tag".to_string() } else { "start_tag".to_string() },
        name,
        if is_end {
            BTreeMap::new()
        } else {
            parse_attributes(stripped, end)
        },
        stripped.trim_end().ends_with('/'),
    )
}

fn parse_attributes(content: &str, start: usize) -> BTreeMap<String, String> {
    let mut attributes: BTreeMap<String, String> = BTreeMap::new();
    let chars: Vec<char> = content.chars().collect();
    let mut cursor = start;
    while cursor < chars.len() {
        while cursor < chars.len() && (chars[cursor].is_whitespace() || chars[cursor] == '/') {
            cursor += 1;
        }
        let Some((name, name_end)) = match_attribute_name_at(&chars, cursor) else {
            cursor += 1;
            continue;
        };
        cursor = name_end;
        while cursor < chars.len() && chars[cursor].is_whitespace() {
            cursor += 1;
        }
        let mut value = String::new();
        if cursor < chars.len() && chars[cursor] == '=' {
            cursor += 1;
            while cursor < chars.len() && chars[cursor].is_whitespace() {
                cursor += 1;
            }
            if cursor < chars.len() && (chars[cursor] == '\'' || chars[cursor] == '"') {
                let quote = chars[cursor];
                cursor += 1;
                let value_start = cursor;
                let mut escaped = false;
                while cursor < chars.len() {
                    if escaped {
                        escaped = false;
                    } else if chars[cursor] == '\\' {
                        escaped = true;
                    } else if chars[cursor] == quote {
                        break;
                    }
                    cursor += 1;
                }
                value = chars[value_start..cursor.min(chars.len())].iter().collect();
                if cursor < chars.len() {
                    cursor += 1;
                }
            } else {
                let value_start = cursor;
                while cursor < chars.len() && !chars[cursor].is_whitespace() && chars[cursor] != '>' && chars[cursor] != '/'
                {
                    cursor += 1;
                }
                value = chars[value_start..cursor].iter().collect();
            }
        }
        attributes.insert(name, value);
    }
    attributes
}

fn expressions_in_region(
    raw: &str,
    absolute_start: usize,
    line_map: &LineMap,
    full_text: &str,
    budgets: &ResourceBudgets,
) -> Vec<JspExpression> {
    let mut expressions: Vec<JspExpression> = Vec::new();
    let raw_text = CharText::new(raw);
    let mut cursor = 0usize;
    while cursor < raw_text.chars.len() {
        let positions: Vec<isize> = [
            raw_text.find_str("${", cursor),
            raw_text.find_str("#{", cursor),
        ]
        .into_iter()
        .filter(|value| *value >= 0)
        .collect();
        if positions.is_empty() {
            break;
        }
        let start = positions.into_iter().min().unwrap() as usize;
        let close = find_el_close(&raw_text, start + 2);
        let end = if close < 0 {
            raw_text.chars.len()
        } else {
            (close as usize) + 1
        };
        let value = raw_text.slice(start, end);
        let span = line_map.span(full_text, absolute_start + start, absolute_start + end);
        expressions.push(JspExpression {
            kind: "el".into(),
            raw: value.clone(),
            span: span.clone(),
            el: Some(parse_el_expression(&value, &line_map.file_path, span.start_line, span.start_column, budgets)),
        });
        cursor = end;
    }
    expressions
}

fn normalize_regions(regions: &mut [JspRegion], syntax: &str) -> (Vec<JspRegion>, BTreeMap<String, String>) {
    let mut taglibs: BTreeMap<String, String> = BTreeMap::new();
    for region in regions.iter() {
        if region.kind == "directive" && region.name.to_lowercase() == "taglib" {
            let prefix = region.attributes.get("prefix").cloned().unwrap_or_default();
            let uri = region
                .attributes
                .get("uri")
                .cloned()
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| region.attributes.get("tagdir").cloned().unwrap_or_default());
            if !prefix.is_empty() && !uri.is_empty() {
                taglibs.insert(prefix, uri);
            }
        }
        if region.kind == "start_tag" {
            for (name, value) in &region.attributes {
                if name == "xmlns" {
                    taglibs.insert(String::new(), value.clone());
                } else if let Some(prefix) = name.strip_prefix("xmlns:") {
                    taglibs.insert(prefix.to_string(), value.clone());
                }
            }
        }
    }
    let mut normalized: Vec<JspRegion> = Vec::new();
    for region in regions.iter() {
        if (region.kind != "start_tag" && region.kind != "end_tag") || region.name.is_empty() {
            normalized.push(region.clone());
            continue;
        }
        let (prefix, local) = split_tag_name(&region.name);
        let uri = taglibs.get(&prefix).cloned().unwrap_or_default();
        if syntax == "jspx" && JSP_XML_URIS.contains(&uri.as_str()) && local.starts_with("directive.") {
            let directive_name = local.split_once('.').map(|(_, rest)| rest).unwrap_or("").to_string();
            let mut new_region = region.clone();
            new_region.kind = "directive".to_string();
            new_region.name = directive_name.clone();
            new_region.taglib_uri = uri.clone();
            normalized.push(new_region);
            if directive_name == "taglib" {
                let declared_prefix = region.attributes.get("prefix").cloned().unwrap_or_default();
                let declared_uri = region.attributes.get("uri").cloned().unwrap_or_default();
                if !declared_prefix.is_empty() && !declared_uri.is_empty() {
                    taglibs.insert(declared_prefix, declared_uri);
                }
            }
            continue;
        }
        let semantic = if (prefix == "jsp" && uri.is_empty()) || JSP_XML_URIS.contains(&uri.as_str()) {
            format!("jsp_{}", local.to_lowercase())
        } else if JSTL_CORE_URIS.contains(&uri.as_str()) {
            format!("jstl_core_{}", local.to_lowercase())
        } else if JSTL_FORMAT_URIS.contains(&uri.as_str()) {
            format!("jstl_format_{}", local.to_lowercase())
        } else if JSTL_FUNCTION_URIS.contains(&uri.as_str()) {
            format!("jstl_function_{}", local.to_lowercase())
        } else {
            String::new()
        };
        let mut new_region = region.clone();
        new_region.taglib_uri = uri;
        new_region.semantic_kind = semantic;
        normalized.push(new_region);
    }
    if syntax == "jspx" {
        normalized = normalize_jspx_islands(normalized);
    }
    (normalized, taglibs)
}

fn normalize_jspx_islands(regions: Vec<JspRegion>) -> Vec<JspRegion> {
    let mut normalized = regions;
    let island_kinds: BTreeMap<&str, &str> = BTreeMap::from([
        ("jsp_declaration", "declaration"),
        ("jsp_expression", "expression"),
        ("jsp_scriptlet", "scriptlet"),
    ]);
    for index in 0..normalized.len() {
        let region = normalized[index].clone();
        let Some(target_kind) = island_kinds.get(region.semantic_kind.as_str()) else {
            continue;
        };
        if region.kind != "start_tag" {
            continue;
        }
        let mut depth = 1i32;
        for close_index in index + 1..normalized.len() {
            let candidate = &normalized[close_index];
            if candidate.name != region.name {
                continue;
            }
            if candidate.kind == "start_tag" && !candidate.self_closing {
                depth += 1;
            } else if candidate.kind == "end_tag" {
                depth -= 1;
                if depth == 0 {
                    for content in normalized[index + 1..close_index].iter_mut() {
                        if content.kind == "template" {
                            *content = JspRegion {
                                kind: (*target_kind).to_string(),
                                name: region.name.clone(),
                                ..content.clone()
                            };
                        }
                    }
                    break;
                }
            }
        }
    }
    normalized
}

fn extract_targets(root: &str, file_path: &str, regions: &[JspRegion]) -> (Vec<JspTarget>, Vec<JspDependency>) {
    let mut targets: Vec<JspTarget> = Vec::new();
    let mut dependencies: Vec<JspDependency> = Vec::new();
    for region in regions {
        if region.kind == "directive" && region.name.to_lowercase() == "include" {
            add_target(
                root,
                file_path,
                &mut targets,
                &mut dependencies,
                "include",
                region.attributes.get("file").cloned().unwrap_or_default(),
                &region.span,
                "translation_include",
                "",
                false,
            );
            continue;
        }
        if region.kind != "start_tag" {
            continue;
        }
        let (prefix, local) = split_tag_name(&region.name);
        let local_lower = local.to_lowercase();
        if region.semantic_kind.starts_with("jsp_") {
            if local_lower == "include" {
                add_target(
                    root,
                    file_path,
                    &mut targets,
                    &mut dependencies,
                    "include",
                    region.attributes.get("page").cloned().unwrap_or_default(),
                    &region.span,
                    "runtime_include",
                    "",
                    false,
                );
            } else if local_lower == "forward" {
                add_target(
                    root,
                    file_path,
                    &mut targets,
                    &mut dependencies,
                    "forward",
                    region.attributes.get("page").cloned().unwrap_or_default(),
                    &region.span,
                    "",
                    "",
                    false,
                );
            }
        } else if JSTL_CORE_URIS.contains(&region.taglib_uri.as_str()) {
            let value = region
                .attributes
                .get("url")
                .cloned()
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| region.attributes.get("value").cloned().unwrap_or_default());
            if local_lower == "import" {
                add_target(root, file_path, &mut targets, &mut dependencies, "include", value, &region.span, "jstl_import", "", false);
            } else if local_lower == "redirect" {
                add_target(root, file_path, &mut targets, &mut dependencies, "redirect", value, &region.span, "", "", false);
            } else if local_lower == "url" {
                add_target(root, file_path, &mut targets, &mut dependencies, "link", value, &region.span, "", "", false);
            }
        } else if prefix.is_empty() {
            if local_lower == "form" {
                let method = region
                    .attributes
                    .get("method")
                    .cloned()
                    .filter(|value| !value.is_empty())
                    .unwrap_or_else(|| "get".to_string())
                    .to_uppercase();
                add_target(
                    root,
                    file_path,
                    &mut targets,
                    &mut dependencies,
                    "form",
                    region.attributes.get("action").cloned().unwrap_or_default(),
                    &region.span,
                    "",
                    &method,
                    false,
                );
            } else if local_lower == "a" {
                add_target(
                    root,
                    file_path,
                    &mut targets,
                    &mut dependencies,
                    "link",
                    region.attributes.get("href").cloned().unwrap_or_default(),
                    &region.span,
                    "",
                    "",
                    false,
                );
            } else if let Some(attribute) = resource_attribute(&local_lower) {
                add_target(
                    root,
                    file_path,
                    &mut targets,
                    &mut dependencies,
                    "resource",
                    region.attributes.get(attribute).cloned().unwrap_or_default(),
                    &region.span,
                    "",
                    "",
                    false,
                );
            }
        }
    }
    (targets, dependencies)
}

#[allow(clippy::too_many_arguments)]
fn add_target(
    root: &str,
    file_path: &str,
    targets: &mut Vec<JspTarget>,
    dependencies: &mut Vec<JspDependency>,
    kind: &str,
    raw_value: String,
    span: &SourceSpan,
    dependency_kind: &str,
    method: &str,
    force_dynamic: bool,
) {
    let value = raw_value.trim().to_string();
    if value.is_empty() {
        return;
    }
    let dynamic = force_dynamic || is_dynamic(&value);
    let mut classification;
    let status;
    let resolved;
    if dynamic {
        classification = "dynamic".to_string();
        status = "dynamic".to_string();
        resolved = String::new();
    } else if value.starts_with("//") {
        classification = "external".to_string();
        status = "external".to_string();
        resolved = String::new();
    } else if value.starts_with('#') {
        classification = "fragment".to_string();
        status = "resolved".to_string();
        resolved = String::new();
    } else if value.starts_with('?') {
        classification = "current".to_string();
        status = "resolved".to_string();
        resolved = file_path.to_string();
    } else {
        classification = if value.starts_with('/') {
            "context_relative".to_string()
        } else {
            "relative".to_string()
        };
        let resolution = resolve_project_path(root, &value, file_path, value.starts_with('/'), false);
        status = resolution.status.clone();
        resolved = resolution.relative_path.clone();
        if status == "external" {
            classification = "external".to_string();
        } else if status == "rejected" || status == "invalid" {
            classification = "rejected".to_string();
        }
    }
    targets.push(JspTarget {
        kind: kind.to_string(),
        raw_value: value.clone(),
        resolved_path: resolved.clone(),
        classification,
        resolution_status: status.clone(),
        span: span.clone(),
        method: method.to_string(),
        source_name: kind.to_string(),
        dynamic,
    });
    if !dependency_kind.is_empty() {
        dependencies.push(JspDependency {
            source_path: file_path.to_string(),
            target_path: resolved,
            kind: dependency_kind.to_string(),
            dynamic,
            resolution_status: status,
            span: span.clone(),
            raw_target: value,
        });
    }
}

fn extract_scriptlet_operations(
    root: &str,
    file_path: &str,
    regions: &[JspRegion],
) -> (Vec<JspScriptletOperation>, Vec<JspTarget>) {
    let mut operations: Vec<JspScriptletOperation> = Vec::new();
    let mut targets: Vec<JspTarget> = Vec::new();
    let dispatcher_re = Regex::new(
        r#"(?s)getRequestDispatcher\s*\(\s*(?P<arg>"(?:\\.|[^"])*"|'(?:\\.|[^'])*'|[^)]*)\s*\)\s*(?:\.\s*(?P<op>forward|include)\s*\()?"#,
    )
    .unwrap();
    let redirect_re =
        Regex::new(r#"(?s)sendRedirect\s*\(\s*(?P<arg>"(?:\\.|[^"])*"|'(?:\\.|[^'])*'|[^)]*)\s*\)"#).unwrap();
    let state_re = Regex::new(
        r#"(?s)(?P<receiver>request(?:\s*\.\s*getSession\s*\(\s*\))?|session|application|servletContext)\s*\.\s*(?P<method>getParameter|getAttribute|setAttribute)\s*\(\s*(?P<arg>"(?:\\.|[^"])*"|'(?:\\.|[^'])*'|[^)]*)"#,
    )
    .unwrap();
    for region in regions {
        if region.kind != "scriptlet" {
            continue;
        }
        let raw = if region.raw.starts_with("<%") {
            if region.raw.ends_with("%>") {
                region.raw[2..region.raw.len() - 2].to_string()
            } else {
                region.raw[2..].to_string()
            }
        } else {
            region.raw.clone()
        };
        for captures in dispatcher_re.captures_iter(&raw) {
            let arg = captures.name("arg").map(|m| m.as_str()).unwrap_or("");
            let (value, status) = literal_argument(arg);
            let operation = captures
                .name("op")
                .map(|m| m.as_str())
                .unwrap_or("dispatcher");
            operations.push(JspScriptletOperation {
                kind: operation.to_string(),
                scope: "request".into(),
                name: value.clone(),
                raw: captures.get(0).map(|m| m.as_str()).unwrap_or("").to_string(),
                resolution_status: status.clone(),
                span: region.span.clone(),
            });
            if operation == "forward" || operation == "include" {
                let mut temporary_dependencies: Vec<JspDependency> = Vec::new();
                let fallback = captures.name("arg").map(|m| m.as_str()).unwrap_or("");
                add_target(
                    root,
                    file_path,
                    &mut targets,
                    &mut temporary_dependencies,
                    operation,
                    if value.is_empty() { fallback.to_string() } else { value.clone() },
                    &region.span,
                    "",
                    "",
                    status != "resolved",
                );
            }
        }
        for captures in redirect_re.captures_iter(&raw) {
            let arg = captures.name("arg").map(|m| m.as_str()).unwrap_or("");
            let (value, status) = literal_argument(arg);
            operations.push(JspScriptletOperation {
                kind: "redirect".into(),
                scope: "response".into(),
                name: value.clone(),
                raw: captures.get(0).map(|m| m.as_str()).unwrap_or("").to_string(),
                resolution_status: status.clone(),
                span: region.span.clone(),
            });
            let mut temporary_dependencies: Vec<JspDependency> = Vec::new();
            let fallback = captures.name("arg").map(|m| m.as_str()).unwrap_or("");
            add_target(
                root,
                file_path,
                &mut targets,
                &mut temporary_dependencies,
                "redirect",
                if value.is_empty() { fallback.to_string() } else { value },
                &region.span,
                "",
                "",
                status != "resolved",
            );
        }
        for captures in state_re.captures_iter(&raw) {
            let receiver_raw = captures.name("receiver").map(|m| m.as_str()).unwrap_or("");
            let receiver: String = receiver_raw.split_whitespace().collect();
            let method = captures.name("method").map(|m| m.as_str()).unwrap_or("");
            let arg = captures.name("arg").map(|m| m.as_str()).unwrap_or("");
            let (value, status) = literal_argument(arg);
            let mut scope = if receiver == "session" || receiver.contains("getSession") {
                "session"
            } else {
                "request"
            };
            if receiver == "application" || receiver == "servletContext" {
                scope = "application";
            }
            let operation = if method == "setAttribute" {
                "write_state"
            } else {
                "read_state"
            };
            if method == "getParameter" {
                scope = "parameter";
            }
            operations.push(JspScriptletOperation {
                kind: operation.to_string(),
                scope: scope.to_string(),
                name: value,
                raw: captures.get(0).map(|m| m.as_str()).unwrap_or("").to_string(),
                resolution_status: status,
                span: region.span.clone(),
            });
        }
        static GET_COOKIES_RE: std::sync::LazyLock<Regex> =
            std::sync::LazyLock::new(|| Regex::new(r"\bgetCookies\s*\(").unwrap());
        let get_cookies_re = &*GET_COOKIES_RE;
        if get_cookies_re.is_match(&raw) {
            operations.push(JspScriptletOperation {
                kind: "read_state".into(),
                scope: "cookie".into(),
                name: String::new(),
                raw: "getCookies()".into(),
                resolution_status: "dynamic".into(),
                span: region.span.clone(),
            });
        }
        static COOKIE_RE: std::sync::LazyLock<Regex> =
            std::sync::LazyLock::new(|| Regex::new(r"new\s+Cookie\s*\(\s*([^,)]+)").unwrap());
        let cookie_re = &*COOKIE_RE;
        for captures in cookie_re.captures_iter(&raw) {
            let arg = captures.get(1).map(|m| m.as_str()).unwrap_or("");
            let (value, status) = literal_argument(arg);
            operations.push(JspScriptletOperation {
                kind: "write_state".into(),
                scope: "cookie".into(),
                name: value,
                raw: captures.get(0).map(|m| m.as_str()).unwrap_or("").to_string(),
                resolution_status: status,
                span: region.span.clone(),
            });
        }
    }
    (operations, targets)
}

fn literal_argument(raw: &str) -> (String, String) {
    let value = raw.trim();
    let chars: Vec<char> = value.chars().collect();
    if chars.len() >= 2 && chars[0] == chars[chars.len() - 1] && (chars[0] == '\'' || chars[0] == '"') {
        return (value[1..value.len() - 1].to_string(), "resolved".to_string());
    }
    (String::new(), "dynamic".to_string())
}

fn split_tag_name(name: &str) -> (String, String) {
    match name.split_once(':') {
        Some((prefix, local)) => (prefix.to_string(), local.to_string()),
        None => (String::new(), name.to_string()),
    }
}

fn is_dynamic(value: &str) -> bool {
    value.contains("${") || value.contains("#{") || value.contains("<%=") || value.contains("<%")
}

fn malformed_diagnostic(file_path: &str, span: SourceSpan, kind: &str) -> Diagnostic {
    Diagnostic::new(
        "servlet_jsp.jsp.unclosed_region",
        &format!("Unclosed {kind}; parser recovered at the next safe boundary"),
        "warning",
        file_path,
        span.start_line,
        span.end_line,
    )
}

/// JSON sink giữ cho unused balance.
#[allow(dead_code)]
fn unused(_: &Value) {}

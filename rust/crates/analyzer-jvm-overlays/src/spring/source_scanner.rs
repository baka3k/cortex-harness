//! Port `tools/spring/source_scanner.py` — regex-based Java/Kotlin source
//! scan (SourceUnit/SourceClass/SourceMethod/SourceAnnotation).

use regex::Regex;
use serde_json::Value;

use super::annotation_catalog::short_annotation_name;
use super::models::SourceSpan;
use super::value_resolver::parse_annotation_args as parse_args;
use crate::pyutil::read_limited;
use std::collections::BTreeMap;
use std::path::Path;

#[derive(Debug, Clone)]
pub struct SourceAnnotation {
    pub name: String,
    pub raw_args: String,
    pub raw: String,
    pub line: i64,
    pub args: BTreeMap<String, Value>,
}

impl SourceAnnotation {
    pub fn short_name(&self) -> String {
        short_annotation_name(&self.name)
    }
}

#[derive(Debug, Clone, Default)]
pub struct SourceMethod {
    pub name: String,
    pub return_type: String,
    pub params: String,
    pub annotations: Vec<SourceAnnotation>,
    pub source: SourceSpan,
    pub code: String,
    pub class_name: String,
    pub package_name: String,
    pub language: String,
    pub file_path: String,
}

impl SourceMethod {
    pub fn qualified_name(&self) -> String {
        let mut parts: Vec<&str> = Vec::new();
        if !self.package_name.is_empty() {
            parts.push(&self.package_name);
        }
        if !self.class_name.is_empty() {
            parts.push(&self.class_name);
        }
        parts.push(&self.name);
        parts.join(".")
    }

    pub fn arity(&self) -> usize {
        count_parameters(&self.params)
    }

    pub fn symbol_id(&self) -> String {
        format!("{}/{}@{}", self.qualified_name(), self.arity(), self.file_path)
    }
}

#[derive(Debug, Clone, Default)]
pub struct SourceClass {
    pub name: String,
    pub declaration_kind: String,
    pub annotations: Vec<SourceAnnotation>,
    pub source: SourceSpan,
    pub header: String,
    pub code: String,
    pub methods: Vec<SourceMethod>,
    pub language: String,
    pub file_path: String,
    pub package_name: String,
}

impl SourceClass {
    pub fn qualified_name(&self) -> String {
        if !self.package_name.is_empty() {
            format!("{}.{}", self.package_name, self.name)
        } else {
            self.name.clone()
        }
    }

    pub fn symbol_id(&self) -> String {
        self.qualified_name()
    }
}

#[derive(Debug, Clone, Default)]
pub struct SourceUnit {
    pub language: String,
    pub file_path: String,
    pub package_name: String,
    pub classes: Vec<SourceClass>,
    pub top_level_methods: Vec<SourceMethod>,
    pub text: String,
}

pub fn scan_source_units(root: &Path, rel_paths: &[String]) -> Vec<SourceUnit> {
    let mut sorted: Vec<&String> = rel_paths.iter().collect();
    sorted.sort();
    sorted.dedup();
    let mut units = Vec::new();
    for rel_path in sorted {
        let language = if rel_path.ends_with(".java") {
            "java"
        } else if rel_path.ends_with(".kt") || rel_path.ends_with(".kts") {
            "kotlin"
        } else {
            continue;
        };
        let text = read_limited(&root.join(rel_path), 2 * 1024 * 1024);
        units.push(scan_source_text(&text, rel_path, language));
    }
    units
}

struct Patterns {
    annotation_start: Regex,
    class_re: Regex,
    kotlin_fun: Regex,
    java_method: Regex,
    package_re: Regex,
}

fn patterns() -> &'static Patterns {
    use std::sync::OnceLock;
    static PATTERNS: OnceLock<Patterns> = OnceLock::new();
    PATTERNS.get_or_init(|| Patterns {
        annotation_start: Regex::new(r"@(?:[A-Za-z_]\w*:)?[A-Za-z_][\w.]*").unwrap(),
        class_re: Regex::new(
            r"\b(?:(?:public|private|protected|abstract|final|open|data|sealed|enum|value)\s+)*(class|interface|record|object|enum\s+class)\s+([A-Za-z_]\w*)(?P<tail>[^{;\n]*)",
        )
        .unwrap(),
        kotlin_fun: Regex::new(
            r"\b(?:suspend\s+)?fun\s+(?:[A-Za-z_][\w.]*\.)?([A-Za-z_]\w*)\s*\(([^)]*)\)\s*(?::\s*([A-Za-z_][\w.<>,? ]*))?",
        )
        .unwrap(),
        java_method: Regex::new(
            r"\b(?:(?:public|private|protected|static|final|abstract|default|synchronized|native|open|override)\s+)*(?P<ret>[A-Za-z_][\w.<>,?\[\] ]+)\s+(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^)]*)\)",
        )
        .unwrap(),
        package_re: Regex::new(r"(?m)^\s*package\s+([A-Za-z_][\w.]*)(?:\s*;)?").unwrap(),
    })
}

pub fn scan_source_text(text: &str, file_path: &str, language: &str) -> SourceUnit {
    let lines: Vec<&str> = splitlines(text);
    let package = extract_package(text);
    let class_ranges = find_class_ranges(&lines);
    let mut classes: Vec<SourceClass> = Vec::new();
    for (start_idx, end_idx, class_name, declaration_kind) in &class_ranges {
        let start_line = start_idx + 1;
        let end_line = end_idx + 1;
        let annotations = collect_preceding_annotations(&lines, *start_idx);
        let class_code = lines[*start_idx..=(*end_idx).min(lines.len() - 1)].join("\n");
        // Python: hàm cho CLASS không truyền class_ranges (skip_ranges rỗng) —
        // chỉ top-level pass mới dùng để bỏ qua method nằm trong class body.
        let methods = find_methods(
            &lines,
            *start_idx,
            *end_idx,
            file_path,
            language,
            class_name,
            &package,
            &[],
        );
        classes.push(SourceClass {
            name: class_name.clone(),
            declaration_kind: declaration_kind.clone(),
            annotations,
            source: SourceSpan::new(file_path, start_line as i64, end_line as i64),
            header: lines[*start_idx].trim().to_string(),
            code: class_code,
            methods,
            language: language.to_string(),
            file_path: file_path.to_string(),
            package_name: package.clone(),
        });
    }

    let top_methods = if lines.is_empty() {
        Vec::new()
    } else {
        find_methods(
            &lines,
            0,
            lines.len() - 1,
            file_path,
            language,
            "",
            &package,
            &class_ranges,
        )
    };
    SourceUnit {
        language: language.to_string(),
        file_path: file_path.to_string(),
        package_name: package,
        classes,
        top_level_methods: top_methods,
        text: text.to_string(),
    }
}

/// Python `str.splitlines()` — tách \n, \r\n, \r (đủ cho corpus text file).
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

fn extract_package(text: &str) -> String {
    patterns()
        .package_re
        .captures(text)
        .and_then(|c| c.get(1))
        .map(|m| m.as_str().to_string())
        .unwrap_or_default()
}

type ClassRange = (usize, usize, String, String);

fn find_class_ranges(lines: &[&str]) -> Vec<ClassRange> {
    let mut ranges: Vec<ClassRange> = Vec::new();
    for (idx, line) in lines.iter().enumerate() {
        if let Some(captures) = patterns().class_re.captures(line) {
            let kind = captures.get(1).unwrap().as_str().to_string();
            let name = captures.get(2).unwrap().as_str().to_string();
            let end_idx = find_block_end(lines, idx);
            ranges.push((idx, end_idx, name, kind.replace(' ', "_")));
        }
    }
    ranges
}

#[allow(clippy::too_many_arguments)]
fn find_methods(
    lines: &[&str],
    start_idx: usize,
    end_idx: usize,
    file_path: &str,
    language: &str,
    class_name: &str,
    package_name: &str,
    class_ranges: &[ClassRange],
) -> Vec<SourceMethod> {
    let mut methods: Vec<SourceMethod> = Vec::new();
    let skip_ranges: Vec<(usize, usize)> = class_ranges.iter().map(|(s, e, _, _)| (*s, *e)).collect();
    let mut idx = start_idx;
    while idx <= end_idx && idx < lines.len() {
        if skip_ranges.iter().any(|(s, e)| *s <= idx && idx <= *e) {
            idx += 1;
            continue;
        }
        let (signature, signature_end) = signature_window(lines, idx);
        let mut name = String::new();
        let mut params = String::new();
        let mut return_type = String::new();
        if language == "kotlin"
            && let Some(captures) = patterns().kotlin_fun.captures(&signature)
        {
            name = captures.get(1).map(|m| m.as_str().to_string()).unwrap_or_default();
            params = captures.get(2).map(|m| m.as_str().to_string()).unwrap_or_default();
            return_type = captures.get(3).map(|m| m.as_str().trim().to_string()).unwrap_or_default();
        }
        if name.is_empty()
            && let Some(captures) = patterns().java_method.captures(&signature)
        {
            name = captures.name("name").map(|m| m.as_str().to_string()).unwrap_or_default();
            params = captures.name("params").map(|m| m.as_str().to_string()).unwrap_or_default();
            return_type = captures
                .name("ret")
                .map(|m| m.as_str().trim().to_string())
                .unwrap_or_default();
        }
        if name.is_empty() {
            idx += 1;
            continue;
        }
        if ["if", "for", "while", "switch", "catch", "return", "new"].contains(&name.as_str()) {
            idx += 1;
            continue;
        }
        let body_end = find_block_end(lines, idx).max(signature_end);
        let annotations = collect_preceding_annotations(lines, idx);
        methods.push(SourceMethod {
            name,
            return_type,
            params,
            annotations,
            source: SourceSpan::new(file_path, (idx + 1) as i64, (body_end + 1) as i64),
            code: lines[idx..=body_end.min(lines.len().saturating_sub(1))].join("\n"),
            class_name: class_name.to_string(),
            package_name: package_name.to_string(),
            language: language.to_string(),
            file_path: file_path.to_string(),
        });
        idx = (idx + 1).max(body_end + 1);
    }
    methods
}

fn signature_window(lines: &[&str], start_idx: usize) -> (String, usize) {
    let mut chunks: Vec<String> = Vec::new();
    let mut depth = 0i32;
    let mut saw_paren = false;
    let upper = lines.len().min(start_idx + 25);
    for (idx, raw_line) in lines.iter().enumerate().take(upper).skip(start_idx) {
        let line = strip_line_comment(raw_line).trim().to_string();
        if line.is_empty() {
            if chunks.is_empty() {
                return (String::new(), start_idx);
            }
            break;
        }
        chunks.push(line.clone());
        depth += line.matches('(').count() as i32;
        if line.contains('(') {
            saw_paren = true;
        }
        depth -= line.matches(')').count() as i32;
        if saw_paren && depth <= 0 {
            return (chunks.join(" "), idx);
        }
        if !saw_paren && ["{", ";", "="].iter().any(|token| line.contains(token)) {
            break;
        }
    }
    (chunks.join(" "), start_idx)
}

fn collect_preceding_annotations(lines: &[&str], declaration_idx: usize) -> Vec<SourceAnnotation> {
    let mut annotations: Vec<SourceAnnotation> = Vec::new();
    let mut idx = declaration_idx as isize - 1;
    let mut buffer: Vec<String> = Vec::new();
    let mut start_line = (declaration_idx + 1) as i64;
    while idx >= 0 {
        let stripped = lines[idx as usize].trim();
        if stripped.is_empty() {
            idx -= 1;
            continue;
        }
        if !stripped.starts_with('@') {
            break;
        }
        buffer.insert(0, stripped.to_string());
        start_line = (idx + 1) as i64;
        idx -= 1;
    }
    for (offset, raw) in buffer.iter().enumerate() {
        annotations.extend(parse_annotation_line(raw, start_line + offset as i64));
    }
    let inline_prefix = lines[declaration_idx].split('{').next().unwrap_or("");
    if inline_prefix.trim().starts_with('@') {
        annotations.extend(parse_annotation_line(inline_prefix, (declaration_idx + 1) as i64));
    }
    annotations
}

fn parse_annotation_line(raw: &str, line: i64) -> Vec<SourceAnnotation> {
    let mut result: Vec<SourceAnnotation> = Vec::new();
    for matched in patterns().annotation_start.find_iter(raw) {
        let name = &matched.as_str()[1..];
        let tail = raw[matched.end()..].trim_start();
        let args = if tail.starts_with('(') { balanced_prefix(tail) } else { String::new() };
        let raw_text = format!("@{name}{args}");
        result.push(SourceAnnotation {
            name: name.to_string(),
            raw_args: args.clone(),
            raw: raw_text,
            line,
            args: parse_args(&args),
        });
    }
    result
}

fn balanced_prefix(text: &str) -> String {
    let mut depth = 0i32;
    let mut quote: Option<char> = None;
    let mut escaped = false;
    let mut out = String::new();
    for ch in text.chars() {
        out.push(ch);
        if escaped {
            escaped = false;
            continue;
        }
        match quote {
            Some(q) => {
                if ch == q {
                    quote = None;
                }
            }
            _ => match ch {
                '\\' => escaped = true,
                '\'' | '"' => quote = Some(ch),
                '(' => depth += 1,
                ')' => {
                    depth -= 1;
                    if depth <= 0 {
                        break;
                    }
                }
                _ => {}
            },
        }
    }
    out
}

fn find_block_end(lines: &[&str], start_idx: usize) -> usize {
    let mut depth = 0i32;
    let mut saw_open = false;
    for (idx, line) in lines.iter().enumerate().skip(start_idx) {
        let stripped = strip_line_comment(line);
        depth += stripped.matches('{').count() as i32;
        if stripped.contains('{') {
            saw_open = true;
        }
        depth -= stripped.matches('}').count() as i32;
        if saw_open && depth <= 0 {
            return idx;
        }
    }
    start_idx
}

fn strip_line_comment(line: &str) -> &str {
    line.split("//").next().unwrap_or(line)
}

fn count_parameters(params: &str) -> usize {
    let text = params.trim();
    if text.is_empty() {
        return 0;
    }
    split_top_level_commas(text).iter().filter(|item| !item.trim().is_empty()).count()
}

/// `_split_top_level_commas` của source_scanner — quote-aware, depth `({[<`.
fn split_top_level_commas(text: &str) -> Vec<String> {
    let mut parts: Vec<String> = Vec::new();
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
                    parts.push(current.trim().to_string());
                    current.clear();
                }
                _ => current.push(ch),
            },
        }
    }
    parts.push(current.trim().to_string());
    parts
}

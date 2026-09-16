//! Common port cho `tools/sql/sql_analyzer.py` và `tools/plsql/plsql_analyzer.py`:
//! masking, comment extraction, routine-end heuristics, call extraction,
//! callee resolution và `LanguageCodeWriter::write_all` pipeline.
//!
//! Hai analyzer dùng regex-scan (không tree-sitter) cho parse; tree-sitter
//! helpers phía Python là dead code cho regex path — không port.

use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};

use regex::Regex;
use serde_json::{json, Map, Value};

use cortex_analyzer_framework::cleanup::cleanup_graph_files;
use cortex_analyzer_framework::cli::AnalyzerArgs;
use cortex_analyzer_framework::embedding_artifact::{self, EmbeddingEmission};
use cortex_analyzer_framework::manifest::load_manifest_paths;
use cortex_graph_writer::language_writer::{FilesVariant, LanguageCodeWriter, WriteAllPayload};

use crate::pyutil::re_escape;

// ── data model (mirror dataclass Python) ────────────────────────────────────

#[derive(Debug, Clone, Default)]
pub struct FunctionDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub scope_name: Option<String>,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub arity: i64,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
    pub exported: bool,
}

#[derive(Debug, Clone, Default)]
pub struct FileDef {
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
    pub imports: Vec<String>,
    pub exports: Vec<String>,
}

#[derive(Debug, Clone, Default)]
pub struct NamespaceDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
}

#[derive(Debug, Clone, Default)]
pub struct RelationEdge {
    pub source_id: String,
    pub source_label: String,
    pub target_id: String,
    pub target_label: String,
    pub rel_type: String,
}

#[derive(Debug, Clone, Default)]
pub struct CallEdge {
    pub caller_id: String,
    pub caller_scope: Option<String>,
    pub callee_name: String,
    pub callee_arity: Option<i64>,
    pub callee_raw: String,
    pub callee_qualified: String,
    pub callee_simple: String,
    pub call_line: i64,
}

#[derive(Debug, Clone, Default)]
pub struct ParsedFile {
    pub functions: Vec<FunctionDef>,
    pub calls: Vec<CallEdge>,
    pub namespaces: Vec<NamespaceDef>,
    pub relations: Vec<RelationEdge>,
    pub file_def: FileDef,
}

// ── text helpers ────────────────────────────────────────────────────────────

/// `_mask_sql_comments` / `_mask_plsql_comments` (identical) — mask comments
/// thành spaces, giữ newline.
pub fn mask_comments(text: &str) -> String {
    let patterns: [Regex; 3] = [
        Regex::new(r"(?s)/\*.*?\*/").unwrap(),
        Regex::new(r"--[^\n]*").unwrap(),
        Regex::new(r"//[^\n]*").unwrap(),
    ];
    let mut out = text.to_string();
    for pattern in &patterns {
        out = pattern
            .replace_all(&out, |caps: &regex::Captures<'_>| {
                caps[0]
                    .chars()
                    .map(|c| if c == '\n' { '\n' } else { ' ' })
                    .collect::<String>()
            })
            .into_owned();
    }
    out
}

/// `_line_from_index` — 1-based.
pub fn line_from_index(text: &str, index: usize) -> i64 {
    text.as_bytes()[..index.min(text.len())]
        .iter()
        .filter(|&&b| b == b'\n')
        .count() as i64
        + 1
}

/// `_snippet_from_span` → (snippet, start_line, end_line).
pub fn snippet_from_span(text: &str, start_idx: usize, end_idx: usize) -> (String, i64, i64) {
    let end = end_idx.min(text.len());
    let start = start_idx.min(end);
    let snippet = text[start..end].to_string();
    let start_line = line_from_index(text, start);
    let end_line = line_from_index(text, end.saturating_sub(1).max(start));
    (snippet, start_line, end_line)
}

/// `_extract_file_comment_from_lines`.
pub fn extract_file_comment_from_lines(lines: &[&str]) -> String {
    let mut comment_lines: Vec<String> = Vec::new();
    let mut in_block = false;
    for line in lines {
        let stripped = line.trim();
        if in_block {
            comment_lines.push((*line).to_string());
            if stripped.contains("*/") {
                in_block = false;
            }
            continue;
        }
        if stripped.starts_with("--") || stripped.starts_with("//") {
            comment_lines.push((*line).to_string());
            continue;
        }
        if stripped.starts_with("/*") {
            comment_lines.push((*line).to_string());
            if !stripped.contains("*/") {
                in_block = true;
            }
            continue;
        }
        if stripped.is_empty() {
            continue;
        }
        break;
    }
    comment_lines
        .iter()
        .map(|line| line.trim())
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

/// `_extract_leading_comment_from_lines` — `start_line` là 1-based def line.
pub fn extract_leading_comment_from_lines(lines: &[&str], start_line: i64) -> String {
    let mut comment_lines: Vec<String> = Vec::new();
    let mut in_block = false;
    let mut idx = start_line - 2; // 0-based index của dòng trước def
    while idx >= 0 {
        let line = lines.get(idx as usize).copied().unwrap_or("");
        let stripped = line.trim();
        if in_block {
            comment_lines.push(line.to_string());
            if stripped.contains("/*") {
                in_block = false;
            }
            idx -= 1;
            continue;
        }
        if stripped.starts_with("--") || stripped.starts_with("//") {
            comment_lines.push(line.to_string());
            idx -= 1;
            continue;
        }
        if stripped.ends_with("*/") || stripped.starts_with("/*") {
            comment_lines.push(line.to_string());
            if !stripped.contains("/*") {
                in_block = true;
            }
            idx -= 1;
            continue;
        }
        if stripped.is_empty() {
            idx -= 1;
            continue;
        }
        break;
    }
    comment_lines
        .iter()
        .rev()
        .map(|line| line.trim())
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

/// `_build_note`.
pub fn build_note(code: &str, comment: &str, summary: &str) -> String {
    let mut parts: Vec<String> = Vec::new();
    if !summary.is_empty() {
        parts.push(format!("Summary:\n{summary}"));
    }
    if !comment.is_empty() {
        parts.push(format!("Comment:\n{comment}"));
    }
    if !code.is_empty() {
        parts.push(format!("Code:\n{code}"));
    }
    parts.join("\n\n")
}

// ── name/lookup helpers ─────────────────────────────────────────────────────

/// `_normalize_call_name`.
pub fn normalize_call_name(text: &str) -> String {
    let re = Regex::new(r"<[^<>]*>").unwrap();
    let mut cleaned = re.replace_all(text, "").into_owned();
    cleaned = cleaned.replace("?.", ".").replace("::", ".");
    let re_ws = Regex::new(r"\s+").unwrap();
    cleaned = re_ws.replace_all(&cleaned, "").into_owned();
    if let Some(pos) = cleaned.rfind('.') {
        cleaned = cleaned[pos + 1..].to_string();
    }
    cleaned.trim().to_string()
}

/// `_normalize_call_parts` → (qualified, simple).
pub fn normalize_call_parts(text: &str) -> (String, String) {
    let re = Regex::new(r"<[^<>]*>").unwrap();
    let mut cleaned = re.replace_all(text, "").into_owned();
    cleaned = cleaned.replace("?.", ".").replace("::", ".");
    let re_ws = Regex::new(r"\s+").unwrap();
    cleaned = re_ws.replace_all(&cleaned, "").into_owned();
    let cleaned = cleaned.trim_matches('.').to_string();
    let simple = if cleaned.is_empty() {
        String::new()
    } else {
        cleaned
            .rsplit('.')
            .next()
            .unwrap_or(&cleaned)
            .to_string()
    };
    (cleaned, simple)
}

/// `_normalize_lookup`.
pub fn normalize_lookup(text: Option<&str>) -> String {
    text.unwrap_or("")
        .trim()
        .replace("::", ".")
        .to_lowercase()
}

/// `_scope_tail`.
pub fn scope_tail(scope_name: Option<&str>) -> String {
    let text = normalize_lookup(scope_name);
    if text.is_empty() {
        return String::new();
    }
    match text.rfind('.') {
        Some(pos) => text[pos + 1..].to_string(),
        None => text,
    }
}

/// `_symbol_id`.
pub fn symbol_id(scope: Option<&str>, name: &str, arity: i64, rel_path: &str) -> String {
    let qualified = match scope {
        Some(scope) => format!("{scope}::{name}"),
        None => name.to_string(),
    };
    format!("{qualified}/{arity}@{rel_path}")
}

/// `_qualified_name`.
pub fn qualified_name(scope: Option<&str>, name: &str) -> String {
    match scope {
        Some(scope) => format!("{scope}::{name}"),
        None => name.to_string(),
    }
}

/// `_namespace_id`.
pub fn namespace_id(name: &str) -> String {
    format!("namespace::{name}")
}

/// `_split_scope` → (scope, name).
pub fn split_scope(qualified_name: &str) -> (Option<String>, String) {
    if let Some(pos) = qualified_name.rfind('.') {
        (
            Some(qualified_name[..pos].trim().to_string()),
            qualified_name[pos + 1..].trim().to_string(),
        )
    } else {
        (None, qualified_name.trim().to_string())
    }
}

// ── routine boundary helpers ────────────────────────────────────────────────

fn end_label_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r#"(?i)\bend\b\s*(?P<label>[A-Za-z_][\w$#\.]*)?\s*;"#).unwrap()
    })
}

/// `_find_definition_end`.
pub fn find_definition_end(masked_text: &str, start_idx: usize, block_end_labels: &[&str]) -> usize {
    let slice = &masked_text[start_idx.min(masked_text.len())..];
    for caps in end_label_re().captures_iter(slice) {
        let label = caps
            .name("label")
            .map(|m| m.as_str().to_lowercase())
            .unwrap_or_default();
        if block_end_labels.contains(&label.as_str()) {
            continue;
        }
        return start_idx + caps.get(0).map(|m| m.end()).unwrap_or(0);
    }
    match masked_text[start_idx.min(masked_text.len())..].find(';') {
        Some(rel) => start_idx + rel + 1,
        None => masked_text.len(),
    }
}

/// `_find_routine_end`.
pub fn find_routine_end(
    masked_text: &str,
    start_idx: usize,
    name: &str,
    block_end_labels: &[&str],
) -> usize {
    let mut candidates = vec![name.to_string()];
    if let Some(pos) = name.rfind('.') {
        candidates.push(name[pos + 1..].to_string());
    }
    let slice = &masked_text[start_idx.min(masked_text.len())..];
    for candidate in &candidates {
        let pattern = Regex::new(&format!(
            r#"(?i)\bend\b\s+{}\s*;"#,
            re_escape(candidate)
        ))
        .unwrap();
        if let Some(m) = pattern.find(slice) {
            return start_idx + m.end();
        }
    }
    let _ = block_end_labels;
    find_definition_end(masked_text, start_idx, block_end_labels)
}

/// `_find_body_start` — trả offset SAU keyword (as|is|begin).
pub fn find_body_start(
    body_start_re: &Regex,
    masked_text: &str,
    start_idx: usize,
    end_idx: usize,
) -> Option<usize> {
    let slice = &masked_text[start_idx.min(masked_text.len())..end_idx.min(masked_text.len())];
    body_start_re.find(slice).map(|m| start_idx + m.end())
}

/// `_extract_paren_segment` — text giữa parens khớp.
pub fn extract_paren_segment(text: &str, open_index: usize) -> Option<String> {
    let bytes = text.as_bytes();
    let mut depth = 0i64;
    let mut in_string: Option<u8> = None;
    let mut idx = open_index;
    while idx < bytes.len() {
        let ch = bytes[idx];
        if let Some(quote) = in_string {
            if ch == quote {
                in_string = None;
            }
            // Python: `elif ch == "\\": continue` — vòng for tự sang char kế,
            // không có state extra → nhánh này là no-op với while-loop.
        } else if ch == b'\'' || ch == b'"' {
            in_string = Some(ch);
        } else if ch == b'(' {
            depth += 1;
        } else if ch == b')' {
            depth -= 1;
            if depth == 0 {
                return Some(text[open_index + 1..idx].to_string());
            }
        }
        idx += 1;
    }
    None
}

/// `_find_matching_paren` — index của paren đóng khớp.
pub fn find_matching_paren(text: &str, open_index: usize) -> Option<usize> {
    let bytes = text.as_bytes();
    let mut depth = 0i64;
    let mut in_string: Option<u8> = None;
    let mut idx = open_index;
    while idx < bytes.len() {
        let ch = bytes[idx];
        if let Some(quote) = in_string {
            if ch == quote {
                in_string = None;
            }
        } else if ch == b'\'' || ch == b'"' {
            in_string = Some(ch);
        } else if ch == b'(' {
            depth += 1;
        } else if ch == b')' {
            depth -= 1;
            if depth == 0 {
                return Some(idx);
            }
        }
        idx += 1;
    }
    None
}

/// `_count_params_segment`.
pub fn count_params_segment(segment: &str) -> i64 {
    if segment.trim().is_empty() {
        return 0;
    }
    let mut depth = 0i64;
    let mut in_string: Option<char> = None;
    let mut count = 0i64;
    let mut has_token = false;
    for ch in segment.chars() {
        if let Some(quote) = in_string {
            if ch == quote {
                in_string = None;
            } else if ch == '\\' {
                continue;
            }
            has_token = true;
            continue;
        }
        match ch {
            '\'' | '"' => {
                in_string = Some(ch);
                has_token = true;
            }
            '(' => {
                depth += 1;
                has_token = true;
            }
            ')' => {
                if depth > 0 {
                    depth -= 1;
                }
                has_token = true;
            }
            ',' if depth == 0 => {
                if has_token {
                    count += 1;
                }
                has_token = false;
            }
            c if !c.is_whitespace() => has_token = true,
            _ => {}
        }
    }
    if has_token {
        count += 1;
    }
    count
}

// ── call extraction ─────────────────────────────────────────────────────────

/// `_extract_calls_from_body` — matcher list + bare-call regex.
pub struct CallExtractors<'a> {
    pub call_re: &'a Regex,
    pub exec_re: &'a Regex,
    pub generic_re: &'a Regex,
    pub bare_re: &'a Regex,
    pub include_generic: bool,
}

#[allow(clippy::too_many_arguments)]
pub fn extract_calls_from_body(
    extractors: &CallExtractors<'_>,
    body_masked: &str,
    keywords: &BTreeSet<String>,
    type_keywords: &BTreeSet<String>,
    builtin_prefixes: &[&str],
    base_line: i64,
) -> Vec<(String, String, String, i64)> {
    let mut calls: Vec<(String, String, String, i64)> = Vec::new();
    let mut seen: BTreeSet<(String, i64)> = BTreeSet::new();

    let mut append_call = |raw_name: &str, start_idx: usize| {
        let (qualified, simple) = normalize_call_parts(raw_name);
        if !is_valid_callee(&simple, keywords, type_keywords) {
            return;
        }
        let call_line =
            base_line + body_masked.as_bytes()[..start_idx.min(body_masked.len())]
                .iter()
                .filter(|&&b| b == b'\n')
                .count() as i64;
        let key = (qualified.to_lowercase(), call_line);
        if seen.contains(&key) {
            return;
        }
        seen.insert(key);
        calls.push((raw_name.trim().to_string(), qualified, simple, call_line));
    };

    for matcher in [extractors.call_re, extractors.exec_re] {
        for m in matcher.captures_iter(body_masked) {
            append_call(&m["name"], m.get(0).map(|g| g.start()).unwrap_or(0));
        }
    }
    if extractors.include_generic {
        for m in extractors.generic_re.captures_iter(body_masked) {
            append_call(&m["name"], m.get(0).map(|g| g.start()).unwrap_or(0));
        }
    }
    for m in extractors.bare_re.captures_iter(body_masked) {
        append_call(&m["name"], m.get(0).map(|g| g.start()).unwrap_or(0));
    }
    let _ = builtin_prefixes;
    calls
}

/// `_is_valid_callee`.
pub fn is_valid_callee(
    simple_name: &str,
    keywords: &BTreeSet<String>,
    type_keywords: &BTreeSet<String>,
) -> bool {
    let token = simple_name.trim().to_lowercase();
    if token.is_empty() {
        return false;
    }
    if keywords.contains(&token) || type_keywords.contains(&token) {
        return false;
    }
    true
}

/// `_is_builtin_callee`.
pub fn is_builtin_callee(
    qualified_name: &str,
    simple_name: &str,
    keywords: &BTreeSet<String>,
    type_keywords: &BTreeSet<String>,
    builtin_prefixes: &[&str],
) -> bool {
    let q = qualified_name.trim().to_lowercase();
    let s = simple_name.trim().to_lowercase();
    if s.is_empty() {
        return true;
    }
    if keywords.contains(&s) || type_keywords.contains(&s) {
        return true;
    }
    if builtin_prefixes.iter().any(|prefix| q.starts_with(prefix)) {
        return true;
    }
    false
}

// ── analyzer spec + pipeline ────────────────────────────────────────────────

/// Tham số hóa khác biệt sql/plsql.
pub struct AnalyzerSpec<'a> {
    pub language_default: &'a str,
    /// `_should_ignore_directory` set.
    pub ignore_dirs: &'static [&'static str],
    /// Union với COMMON_SCAN_EXCLUDE (plsql import; sql không).
    pub use_common_scan_exclude: bool,
    /// `matches_extra_ignore` trong dir filter (plsql có; sql không).
    pub use_extra_ignore: bool,
    /// Scan extensions (".sql", ...).
    pub scan_extensions: &'a [&'a str],
    /// Skip file suffixes cho scan.
    pub skip_suffixes: &'a [&'a str],
    pub skip_names: &'a [&'a str],
    /// "SQL" / "PL/SQL" cho verbose/dry-run lines.
    pub file_label: &'a str,
    /// parse một file → ParsedFile.
    pub parse_file: fn(&Path, &Path) -> ParsedFile,
    /// call extractor (regex sets riêng).
    pub make_extractors: fn() -> CallExtractors<'static>,
    pub keywords: fn() -> BTreeSet<String>,
    pub type_keywords: fn() -> BTreeSet<String>,
    pub builtin_prefixes: &'static [&'static str],
    pub block_end_labels: &'static [&'static str],
}

fn keyword_set(items: &[&str]) -> BTreeSet<String> {
    items.iter().map(|item| item.to_string()).collect()
}

/// Scan + ignore semantics chung (`_scan_*_files`).
pub fn scan_files(root: &Path, spec: &AnalyzerSpec<'_>) -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = Vec::new();
    walk(root, root, spec, &mut files);
    files.sort();
    files
}

fn ignored(dir_name: &str, spec: &AnalyzerSpec<'_>) -> bool {
    if spec.ignore_dirs.contains(&dir_name) {
        return true;
    }
    if spec.use_common_scan_exclude
        && cortex_analyzer_framework::scan::COMMON_SCAN_EXCLUDE.contains(&dir_name)
    {
        return true;
    }
    if spec.use_extra_ignore && cortex_analyzer_framework::scan::matches_extra_ignore(dir_name) {
        return true;
    }
    dir_name.ends_with(".swp") || dir_name.ends_with(".swo")
}

fn walk(root: &Path, dir: &Path, spec: &AnalyzerSpec<'_>, files: &mut Vec<PathBuf>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut subdirs: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        if path.is_dir() {
            if !ignored(&name, spec) {
                subdirs.push(path);
            }
            continue;
        }
        if spec.skip_suffixes.iter().any(|suffix| name.ends_with(suffix)) {
            continue;
        }
        if spec.skip_names.contains(&name.as_str()) {
            continue;
        }
        if spec.scan_extensions.iter().any(|ext| name.ends_with(ext)) {
            files.push(path.clone());
        }
    }
    let _ = root;
    for sub in subdirs {
        walk(root, &sub, spec, files);
    }
}

fn rel_slash(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .components()
        .map(|c| c.as_os_str().to_string_lossy().to_string())
        .collect::<Vec<_>>()
        .join("/")
}

#[derive(Clone)]
struct IndexEntry {
    symbol_id: String,
    scope_key: String,
}

/// Function index của build_call_graph (name/qualified × arity).
struct FunctionIndex {
    by_name: HashMap<String, Vec<IndexEntry>>,
    by_name_arity: HashMap<(String, i64), Vec<IndexEntry>>,
    by_qualified: HashMap<String, Vec<IndexEntry>>,
    by_qualified_arity: HashMap<(String, i64), Vec<IndexEntry>>,
}

fn resolve_callee_id(
    index: &FunctionIndex,
    callee_simple: &str,
    callee_name: &str,
    callee_qualified: &str,
    caller_scope: Option<&str>,
    callee_arity: Option<i64>,
) -> (Option<String>, &'static str) {
    let simple_key = normalize_lookup(Some(if callee_simple.is_empty() {
        callee_name
    } else {
        callee_simple
    }));
    let qualified_key = normalize_lookup(Some(callee_qualified));
    let caller_scope_key = normalize_lookup(caller_scope);

    if !qualified_key.is_empty() {
        let candidates: Option<&Vec<IndexEntry>> = match callee_arity {
            Some(arity) => index.by_qualified_arity.get(&(qualified_key.clone(), arity)),
            None => index.by_qualified.get(&qualified_key),
        };
        if let Some(candidates) = candidates {
            if candidates.len() == 1 {
                return (Some(candidates[0].symbol_id.clone()), "qualified_exact");
            }
            let scoped: Vec<&IndexEntry> = candidates
                .iter()
                .filter(|cand| cand.scope_key == caller_scope_key)
                .collect();
            if scoped.len() == 1 {
                return (Some(scoped[0].symbol_id.clone()), "qualified_scope");
            }
        }
    }

    let candidates: Option<&Vec<IndexEntry>> = match callee_arity {
        Some(arity) => index.by_name_arity.get(&(simple_key.clone(), arity)),
        None => index.by_name.get(&simple_key),
    };
    let Some(candidates) = candidates else {
        return (None, "missing");
    };
    if !caller_scope_key.is_empty() {
        let scoped: Vec<&IndexEntry> = candidates
            .iter()
            .filter(|cand| cand.scope_key == caller_scope_key)
            .collect();
        if scoped.len() == 1 {
            return (Some(scoped[0].symbol_id.clone()), "same_scope");
        }
        let caller_tail = scope_tail(caller_scope);
        let package_scoped: Vec<&IndexEntry> = candidates
            .iter()
            .filter(|cand| scope_tail(Some(&cand.scope_key)) == caller_tail)
            .collect();
        if package_scoped.len() == 1 {
            return (Some(package_scoped[0].symbol_id.clone()), "same_package");
        }
    }
    if candidates.len() == 1 {
        return (Some(candidates[0].symbol_id.clone()), "global_unique");
    }
    (None, "ambiguous")
}

fn scope_props(
    row: &mut Map<String, Value>,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) {
    row.insert("project_id".into(), json!(project_id));
    row.insert("project_name".into(), json!(project_name));
    row.insert("language".into(), json!(language));
    row.insert("repo".into(), json!(repo));
    row.insert("build_system".into(), json!(build_system));
}

fn relation_row(
    source_id: &str,
    target_id: &str,
    rel_type: &str,
    properties: Value,
) -> Map<String, Value> {
    let mut row = Map::new();
    row.insert("source_id".into(), json!(source_id));
    row.insert("target_id".into(), json!(target_id));
    row.insert("rel_type".into(), json!(rel_type));
    row.insert("properties".into(), properties);
    row
}

/// Pipeline `build_call_graph` + `main` của sql/plsql analyzer.
pub fn run(
    args: &AnalyzerArgs,
    spec: &AnalyzerSpec<'_>,
    call_scope: &str,
    neo4j_batch_size: usize,
) -> Result<i32, String> {
    let root = cortex_analyzer_framework::cli::abs_root(&args.root);
    if !root.is_dir() {
        eprintln!("Root not found: {}", args.root);
        return Ok(2);
    }
    let verbose = args.verbose;

    let mut store = if args.graph_writes_disabled() {
        None
    } else {
        Some(args.open_store().map_err(|e| e.to_string())?)
    };

    // ── manifests (main) ────────────────────────────────────────────────────
    let mut changed_files: BTreeSet<String> = BTreeSet::new();
    let mut deleted_files: BTreeSet<String> = BTreeSet::new();
    if args.incremental {
        if let Some(manifest) = &args.changed_files_manifest {
            changed_files = load_manifest_paths(manifest, &root);
        }
        if let Some(manifest) = &args.deleted_files_manifest {
            deleted_files = load_manifest_paths(manifest, &root);
        }
        if verbose {
            println!(
                "[diff] incremental manifests changed={} deleted={}",
                changed_files.len(),
                deleted_files.len()
            );
        }
    }
    if args.incremental && verbose {
        println!("[state] incremental mode disables neo4j resume state");
    }

    // ── dry run ─────────────────────────────────────────────────────────────
    if args.dry_run {
        let mut files = scan_files(&root, spec);
        let manifest_strs: Vec<String> = changed_files.iter().cloned().collect();
        if args.incremental && !changed_files.is_empty() {
            files.retain(|path| changed_files.contains(&rel_slash(&root, path)));
            println!(
                "Dry run (incremental): {} {} files selected (manifest={})",
                files.len(),
                spec.file_label,
                manifest_strs.len()
            );
        } else {
            println!(
                "Dry run: {} {} files found",
                files.len(),
                spec.file_label
            );
        }
        return Ok(0);
    }

    // ── project scope ───────────────────────────────────────────────────────
    let project_id = non_empty(args.project_id.clone())
        .or_else(|| non_empty(args.project_id_alt.clone()))
        .unwrap_or_else(|| basename_of(&root));
    let project_name =
        non_empty(args.project_name.clone()).unwrap_or_else(|| project_id.clone());
    let language = non_empty(args.language.clone())
        .unwrap_or_else(|| spec.language_default.to_string());
    let repo = non_empty(args.repo.clone()).unwrap_or_else(|| root.to_string_lossy().to_string());
    let build_system = args.build_system.clone().unwrap_or_default();

    // ── scan ────────────────────────────────────────────────────────────────
    let all_scanned_files = scan_files(&root, spec);
    let changed_set: BTreeSet<String> = changed_files
        .iter()
        .map(|item| item.replace('\\', "/"))
        .filter(|item| !item.is_empty())
        .collect();
    let deleted_set: BTreeSet<String> = deleted_files
        .iter()
        .map(|item| item.replace('\\', "/"))
        .filter(|item| !item.is_empty())
        .collect();
    let selected_files: Vec<PathBuf> = if args.incremental {
        all_scanned_files
            .iter()
            .filter(|path| changed_set.contains(&rel_slash(&root, path)))
            .cloned()
            .collect()
    } else {
        all_scanned_files.clone()
    };
    if verbose {
        if args.incremental {
            println!(
                "[scan] incremental before={} after={} changed={} deleted={} selected={}/{}",
                if args.commit_sha_before.is_empty() { "unknown" } else { &args.commit_sha_before },
                if args.commit_sha_after.is_empty() { "unknown" } else { &args.commit_sha_after },
                changed_set.len(),
                deleted_set.len(),
                selected_files.len(),
                all_scanned_files.len(),
            );
        }
        println!(
            "[scan] Found {} {} files under {}",
            selected_files.len(),
            spec.file_label,
            args.root
        );
    }
    let total_files = selected_files.len();

    // ── cleanup (changed ∪ deleted) ─────────────────────────────────────────
    let cleanup_targets: Vec<String> = changed_set
        .union(&deleted_set)
        .cloned()
        .collect::<BTreeSet<String>>()
        .into_iter()
        .collect();
    if args.incremental
        && !cleanup_targets.is_empty()
        && let Some(store) = store.as_deref_mut()
    {
        {
            if verbose {
                println!(
                    "[cleanup][graph] deleting graph data for {} files",
                    cleanup_targets.len()
                );
            }
            let (deleted_nodes, deleted_unknown) =
                cleanup_graph_files(store, &project_id, &cleanup_targets).map_err(|e| e.to_string())?;
            if verbose {
                println!(
                    "[cleanup][graph] deleted_nodes={} deleted_unknown_functions={}",
                    deleted_nodes, deleted_unknown
                );
            }
        }
    }

    // ── parse (index build) ─────────────────────────────────────────────────
    let mut payloads: Vec<ParsedFile> = Vec::with_capacity(selected_files.len());
    for (index, file_path) in selected_files.iter().enumerate() {
        if verbose && (index == 0 || (index + 1) % 50 == 0 || index + 1 == total_files) {
            println!("[parse] {}/{}: {}", index + 1, total_files, file_path.display());
        }
        payloads.push((spec.parse_file)(file_path, &root));
    }

    // Function index
    let mut function_index = FunctionIndex {
        by_name: HashMap::new(),
        by_name_arity: HashMap::new(),
        by_qualified: HashMap::new(),
        by_qualified_arity: HashMap::new(),
    };
    for payload in &payloads {
        for func in &payload.functions {
            let name_key = normalize_lookup(Some(&func.name));
            let scope_key = normalize_lookup(func.scope_name.as_deref());
            let qualified_key = normalize_lookup(Some(&func.qualified_name));
            let entry = IndexEntry {
                symbol_id: func.symbol_id.clone(),
                scope_key,
            };
            function_index
                .by_name
                .entry(name_key.clone())
                .or_default()
                .push(entry.clone());
            function_index
                .by_name_arity
                .entry((name_key, func.arity))
                .or_default()
                .push(entry.clone());
            if !qualified_key.is_empty() {
                function_index
                    .by_qualified
                    .entry(qualified_key.clone())
                    .or_default()
                    .push(entry.clone());
                function_index
                    .by_qualified_arity
                    .entry((qualified_key, func.arity))
                    .or_default()
                    .push(entry);
            }
        }
    }

    let sr_fn;
    let sr_cls;
    if let Some(store) = store.take() {
        if verbose {
            println!("[graph] Writing nodes and relations (streaming)...");
        }
        let call_scope = {
            let mode = call_scope.trim().to_lowercase();
            if mode.is_empty() { "internal".to_string() } else { mode }
        };
        let keywords = (spec.keywords)();
        let type_keywords = (spec.type_keywords)();

        let mut all_functions: Vec<Map<String, Value>> = Vec::new();
        let all_types: Vec<Map<String, Value>> = Vec::new();
        let mut all_namespaces: Vec<Map<String, Value>> = Vec::new();
        let mut all_files: Vec<Map<String, Value>> = Vec::new();
        let mut all_relations: Vec<Map<String, Value>> = Vec::new();
        let mut all_calls: Vec<Map<String, Value>> = Vec::new();
        // external_functions: dict insertion order → Vec + set.
        let mut external_functions: Vec<Map<String, Value>> = Vec::new();
        let mut external_ids: BTreeSet<String> = BTreeSet::new();
        let mut known_function_ids: BTreeSet<String> = BTreeSet::new();
        let mut total_calls = 0i64;
        let mut resolved_calls = 0i64;
        let mut unresolved_calls_count = 0i64;
        let mut external_calls_written = 0i64;

        for payload in &payloads {
            let file_def = &payload.file_def;
            let file_id = file_def.file_path.clone();
            let mut file_row = Map::new();
            file_row.insert("id".into(), json!(file_id));
            file_row.insert("path".into(), json!(file_id));
            file_row.insert("start_line".into(), json!(file_def.start_line));
            file_row.insert("end_line".into(), json!(file_def.end_line));
            file_row.insert("code".into(), json!(file_def.code));
            file_row.insert("comment".into(), json!(file_def.comment));
            file_row.insert("summary".into(), json!(file_def.summary));
            file_row.insert("note".into(), json!(file_def.note));
            file_row.insert("imports".into(), json!(file_def.imports));
            file_row.insert("exports".into(), json!(file_def.exports));
            scope_props(
                &mut file_row,
                &project_id,
                &project_name,
                &language,
                &repo,
                &build_system,
            );
            all_files.push(file_row);
            all_relations.push(relation_row(&project_id, &file_id, "CONTAINS", json!({})));
            for ns in &payload.namespaces {
                let mut row = Map::new();
                row.insert("id".into(), json!(ns.symbol_id));
                row.insert("name".into(), json!(ns.name));
                row.insert("qualified_name".into(), json!(ns.qualified_name));
                row.insert("file_path".into(), json!(ns.file_path));
                row.insert("start_line".into(), json!(ns.start_line));
                row.insert("end_line".into(), json!(ns.end_line));
                row.insert("code".into(), json!(ns.code));
                row.insert("comment".into(), json!(ns.comment));
                row.insert("summary".into(), json!(ns.summary));
                row.insert("note".into(), json!(ns.note));
                scope_props(&mut row, &project_id, &project_name, &language, &repo, &build_system);
                all_namespaces.push(row);
                all_relations.push(relation_row(&file_id, &ns.symbol_id, "CONTAINS", json!({})));
            }
            for func in &payload.functions {
                known_function_ids.insert(func.symbol_id.clone());
                let mut row = Map::new();
                row.insert("id".into(), json!(func.symbol_id));
                row.insert("name".into(), json!(func.name));
                row.insert("qualified_name".into(), json!(func.qualified_name));
                row.insert("kind".into(), json!(func.kind));
                row.insert("scope_name".into(), json!(func.scope_name));
                row.insert("class_name".into(), Value::Null);
                row.insert("package_name".into(), Value::Null);
                row.insert("file_path".into(), json!(func.file_path));
                row.insert("start_line".into(), json!(func.start_line));
                row.insert("end_line".into(), json!(func.end_line));
                row.insert("arity".into(), json!(func.arity));
                row.insert("code".into(), json!(func.code));
                row.insert("comment".into(), json!(func.comment));
                row.insert("summary".into(), json!(func.summary));
                row.insert("note".into(), json!(func.note));
                row.insert("exported".into(), json!(func.exported));
                scope_props(&mut row, &project_id, &project_name, &language, &repo, &build_system);
                all_functions.push(row);
                all_relations.push(relation_row(&file_id, &func.symbol_id, "CONTAINS", json!({})));
            }
            for rel in &payload.relations {
                all_relations.push(relation_row(
                    &rel.source_id,
                    &rel.target_id,
                    &rel.rel_type,
                    json!({}),
                ));
            }
            for call in &payload.calls {
                total_calls += 1;
                let (callee_id, reason) = resolve_callee_id(
                    &function_index,
                    &call.callee_simple,
                    &call.callee_name,
                    &call.callee_qualified,
                    call.caller_scope.as_deref(),
                    call.callee_arity,
                );
                if let Some(callee_id) = callee_id {
                    let mut row = Map::new();
                    row.insert("caller_id".into(), json!(call.caller_id));
                    row.insert("callee_id".into(), json!(callee_id));
                    row.insert("call_type".into(), json!("internal"));
                    row.insert("project_id".into(), json!(project_id));
                    all_calls.push(row);
                    resolved_calls += 1;
                    continue;
                }
                let call_kind = call_kind_of(
                    call,
                    &keywords,
                    &type_keywords,
                    spec.builtin_prefixes,
                );
                let should_materialize = match call_scope.trim().to_lowercase().as_str() {
                    "internal" => false,
                    "everything" => true,
                    _ => call_kind != "builtin",
                };
                if should_materialize {
                    let (symbol_id, external_scope, external_name) =
                        build_external_symbol(call);
                    if !known_function_ids.contains(&symbol_id)
                        && !external_ids.contains(&symbol_id)
                    {
                        let is_builtin = call_kind == "builtin";
                        let mut row = Map::new();
                        row.insert("id".into(), json!(symbol_id));
                        row.insert("name".into(), json!(external_name));
                        row.insert(
                            "qualified_name".into(),
                            json!(if call.callee_qualified.is_empty() {
                                external_name.clone()
                            } else {
                                call.callee_qualified.clone()
                            }),
                        );
                        row.insert("kind".into(), json!("external_function"));
                        row.insert("scope_name".into(), json!(external_scope));
                        row.insert("class_name".into(), Value::Null);
                        row.insert("package_name".into(), Value::Null);
                        row.insert("file_path".into(), json!("<external>"));
                        row.insert("start_line".into(), json!(0));
                        row.insert("end_line".into(), json!(0));
                        row.insert("arity".into(), json!(call.callee_arity.unwrap_or(0)));
                        row.insert("code".into(), json!(""));
                        row.insert("comment".into(), json!(""));
                        row.insert(
                            "summary".into(),
                            json!("External callee inferred from SQL callsite"),
                        );
                        row.insert("note".into(), json!(""));
                        row.insert("exported".into(), json!(false));
                        row.insert("external".into(), json!(true));
                        row.insert("builtin".into(), json!(is_builtin));
                        scope_props(
                            &mut row,
                            &project_id,
                            &project_name,
                            &language,
                            &repo,
                            &build_system,
                        );
                        external_ids.insert(symbol_id.clone());
                        external_functions.push(row);
                    }
                    let mut row = Map::new();
                    row.insert("caller_id".into(), json!(call.caller_id));
                    row.insert("callee_id".into(), json!(symbol_id));
                    row.insert("call_type".into(), json!(call_kind));
                    row.insert("project_id".into(), json!(project_id));
                    all_calls.push(row);
                    resolved_calls += 1;
                    external_calls_written += 1;
                } else {
                    unresolved_calls_count += 1;
                    let _ = reason;
                }
            }
        }
        all_functions.extend(external_functions);

        let mut writer = LanguageCodeWriter::new(
            store,
            args.neo4j_db.clone(),
            neo4j_batch_size.max(1),
            verbose,
        );
        let payload = WriteAllPayload {
            projects: &project_rows(&project_id, &project_name, &language, &repo, &args.root, &build_system),
            packages: &[],
            namespaces: &all_namespaces,
            files: &all_files,
            classes: &[],
            types: &all_types,
            function_types: &[],
            functions: &all_functions,
            fields: &[],
            aliases: &[],
            templates: &[],
            relations: &all_relations,
            calls: &all_calls,
            calls_with_site: &[],
            properties: &[],
            events: &[],
            interfaces: &[],
            enums: &[],
            constants: &[],
            variables: &[],
            navigators: &[],
            has_routes: &[],
            param_lists: &[],
            workflows: &[],
            workflow_steps: &[],
            call_evidence_sites: &[],
            call_evidence_observations: &[],
            build_configurations: &[],
            semantic_coverage: &[],
            proc_function_joins: &[],
            proc_host_declarations: &[],
            use_full_writers: true,
            files_variant: FilesVariant::WithImports,
        };
        // Phase-02: capture embedding categories BEFORE write_all consumes
        // (plan `260916-1432-legacy-17-vector-emit`).
        let embedding_categories = payload.embedding_categories();
        let counts = writer.write_all(&payload);
        if let Err(error) = counts {
            return Err(format!("[graph] write failed: {error}"));
        }
        // Phase-02: emit EmbeddingInputArtifact.
        if let Some(output) = args.embedding_input_output() {
            let files_selected: Vec<String> = selected_files
                .iter()
                .map(|path| rel_slash(&root, path))
                .collect();
            if let Err(error) = embedding_artifact::maybe_emit_embedding_artifact(
                Some(output),
                EmbeddingEmission {
                    parser: spec.language_default,
                    project_id: &project_id,
                    root_scope: &repo,
                    full_replace: !args.incremental,
                    scanned_directory: true,
                    files_selected,
                    files_deleted: deleted_set.iter().cloned().collect(),
                    categories: embedding_categories,
                },
            ) {
                eprintln!("sql embedding-input artifact failed: {error}");
                return Ok(1);
            }
        }
        if verbose {
            let ratio = if total_calls > 0 {
                ((resolved_calls as f64 / total_calls as f64) * 100.0 * 100.0).round() / 100.0
            } else {
                0.0
            };
            println!(
                "[calls] resolved {}/{} ({:.2}%), unresolved {}, external {}",
                resolved_calls, total_calls, ratio, unresolved_calls_count, external_calls_written
            );
        }
        if verbose {
            println!("[graph] Write complete");
        }
        sr_fn = all_functions.len();
        sr_cls = all_types.len();
    } else {
        sr_fn = 0;
        sr_cls = 0;
    }

    println!(
        "[SCAN_RESULT] parser={language} files={total_files} functions={sr_fn} classes={sr_cls}"
    );
    Ok(0)
}

fn project_rows(
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    root_raw: &str,
    build_system: &str,
) -> Vec<Map<String, Value>> {
    let mut row = Map::new();
    row.insert("id".into(), json!(project_id));
    row.insert("name".into(), json!(project_name));
    row.insert("language".into(), json!(language));
    row.insert("repo".into(), json!(repo));
    row.insert("root".into(), json!(root_raw));
    row.insert("build_system".into(), json!(build_system));
    vec![row]
}

fn call_kind_of(
    call: &CallEdge,
    keywords: &BTreeSet<String>,
    type_keywords: &BTreeSet<String>,
    builtin_prefixes: &[&str],
) -> String {
    let qualified = if !call.callee_qualified.is_empty() {
        call.callee_qualified.clone()
    } else if !call.callee_raw.is_empty() {
        call.callee_raw.clone()
    } else {
        call.callee_name.clone()
    };
    let simple = if !call.callee_simple.is_empty() {
        call.callee_simple.clone()
    } else {
        call.callee_name.clone()
    };
    if is_builtin_callee(&qualified, &simple, keywords, type_keywords, builtin_prefixes) {
        "builtin".to_string()
    } else {
        "external".to_string()
    }
}

fn build_external_symbol(call: &CallEdge) -> (String, String, String) {
    let qualified = call.callee_qualified.trim();
    let simple = if !call.callee_simple.trim().is_empty() {
        call.callee_simple.trim()
    } else {
        call.callee_name.trim()
    };
    let base = if !qualified.is_empty() {
        qualified.to_string()
    } else if !simple.is_empty() {
        simple.to_string()
    } else {
        "unknown_external".to_string()
    };
    let key = {
        let normalized = normalize_lookup(Some(&base));
        if normalized.is_empty() {
            "unknown_external".to_string()
        } else {
            normalized
        }
    };
    let symbol_id = format!("external::{key}");
    if base.contains('.') {
        let (scope_name, display_name) = split_scope(&base);
        (symbol_id, scope_name.unwrap_or_else(|| "<external>".to_string()), display_name)
    } else {
        (symbol_id, "<external>".to_string(), base)
    }
}

fn non_empty(value: Option<String>) -> Option<String> {
    value.filter(|v| !v.is_empty())
}

fn basename_of(root: &Path) -> String {
    root.file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| root.to_string_lossy().to_string())
}

/// Regex keyword-set builders dùng chung cho spec.
pub fn make_keyword_set(items: &[&str]) -> BTreeSet<String> {
    keyword_set(items)
}

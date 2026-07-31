//! SQL pure-regex extraction — Phase 2 (Tier 3) port of `sql_analyzer.py`.
//!
//! SQL is **Family B** 6-tuple but uses `ClassDef` (not `TypeDef`) for the
//! types it sees. Crucially, the Python source uses **pure regex** for every
//! extraction step — the `tree_sitter` scaffold is dead code in the upstream
//! and is omitted here.
//!
//! Returns 6-tuple: `functions, calls, classes, namespaces, relations, file_def`.
//!
//! Dataclass differences from JS/PHP:
//! - `FunctionDef` has `exported: bool` (default false).
//! - `CallEdge` has `callee_raw`, `callee_qualified`, `callee_simple`
//!   (the rich set, not just `callee_name`).
//! - `ClassDef` has `exported: bool` (default false).
//! - `RelationEdge` uses `Dict[str, str]` properties.
//!
//! This module is the regex-only port. Tree-sitter scaffolding is intentionally
//! dropped per the upstream plan's "Phase S — drop dead tree-sitter code".
//!
//! Per the plan, when no real corpus is available, the focus is on the regex
//! engine itself. The unit tests exercise the patterns in isolation.

use std::collections::HashSet;

use once_cell::sync::Lazy;
use regex::Regex;

use crate::symbols::{CallEdge, FileDef, FunctionDef, NamespaceDef, ParseMeta, RelationEdge, TypeDef};

// ── Regex patterns ──────────────────────────────────────────────────────

const SQL_IDENTIFIER: &str = r"[A-Za-z_][\w$#]*";
const SQL_QUALIFIED_IDENTIFIER: &str = "(?:[A-Za-z_][\\w$#]*\\.)*[A-Za-z_][\\w$#]*";

static SQL_CREATE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)\bcreate\s+(?:or\s+replace\s+)?(?P<kind>procedure|proc|function)\s+(?P<name>(?:[A-Za-z_][\w$#]*\.)*[A-Za-z_][\w$#]*)",
    )
    .unwrap()
});

static SQL_CALL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\bcall\s+(?P<name>(?:[A-Za-z_][\w$#]*\.)*[A-Za-z_][\w$#]*)").unwrap()
});

static SQL_EXEC_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\bexec(?:ute)?\s+(?P<name>(?:[A-Za-z_][\w$#]*\.)*[A-Za-z_][\w$#]*)").unwrap()
});

static SQL_GENERIC_CALL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\b(?P<name>(?:[A-Za-z_][\w$#]*\.)*[A-Za-z_][\w$#]*)\s*\(").unwrap()
});

static SQL_BARE_CALL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?im)^\s*(?P<name>(?:[A-Za-z_][\w$#]*\.)*[A-Za-z_][\w$#]*)\s*;\s*$").unwrap()
});

static SQL_BODY_START_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)\b(as|is|begin)\b").unwrap());

static SQL_END_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\bend\b\s*(?P<label>[A-Za-z_][\w$#\.]*)?\s*;").unwrap()
});

// ── Lookup keyword sets ─────────────────────────────────────────────────

const SQL_CALL_KEYWORDS: &[&str] = &[
    "and", "as", "begin", "by", "case", "create", "declare", "delete", "drop", "else", "elseif",
    "end", "exec", "execute", "from", "function", "group", "having", "if", "insert", "into",
    "join", "left", "limit", "merge", "not", "null", "on", "or", "order", "procedure", "return",
    "right", "select", "set", "then", "truncate", "union", "update", "values", "when", "where",
    "while",
];

const SQL_TYPE_KEYWORDS: &[&str] = &[
    "bigint", "binary", "bit", "blob", "bool", "boolean", "char", "date", "datetime", "decimal",
    "double", "float", "int", "integer", "json", "nchar", "numeric", "nvarchar", "real", "smallint",
    "text", "time", "timestamp", "tinyint", "varchar", "xml",
];

const SQL_BLOCK_END_LABELS: &[&str] = &["if", "loop", "case", "while", "repeat", "for"];

const SQL_BUILTIN_PREFIXES: &[&str] = &[
    "pg_catalog.",
    "information_schema.",
    "sys.",
    "dbms_",
    "utl_",
];

// ── SQL parse output (Family B 6-tuple) ────────────────────────────────

#[derive(Debug, Default)]
pub struct SqlParseOutput {
    pub functions: Vec<FunctionDef>,
    pub calls: Vec<CallEdge>,
    pub classes: Vec<TypeDef>,
    pub namespaces: Vec<NamespaceDef>,
    pub relations: Vec<RelationEdge>,
    pub file_def: FileDef,
}

// ── Helpers ─────────────────────────────────────────────────────────────

fn mask_sql_comments(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let bytes = text.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        // /* ... */ block (DOTALL)
        if i + 1 < bytes.len() && bytes[i] == b'/' && bytes[i + 1] == b'*' {
            let start = i;
            i += 2;
            while i + 1 < bytes.len() && !(bytes[i] == b'*' && bytes[i + 1] == b'/') {
                out.push(if bytes[i] == b'\n' { '\n' } else { ' ' });
                i += 1;
            }
            if i + 1 < bytes.len() {
                out.push(' ');
                out.push(' ');
                i += 2;
            } else {
                // unterminated — preserve rest
                out.push_str(&text[start..]);
                return out;
            }
            continue;
        }
        // -- single line
        if i + 1 < bytes.len() && bytes[i] == b'-' && bytes[i + 1] == b'-' {
            while i < bytes.len() && bytes[i] != b'\n' {
                out.push(' ');
                i += 1;
            }
            continue;
        }
        // // single line
        if i + 1 < bytes.len() && bytes[i] == b'/' && bytes[i + 1] == b'/' {
            while i < bytes.len() && bytes[i] != b'\n' {
                out.push(' ');
                i += 1;
            }
            continue;
        }
        out.push(bytes[i] as char);
        i += 1;
    }
    out
}

fn line_from_index(text: &str, idx: usize) -> u32 {
    text[..idx.min(text.len())].bytes().filter(|b| *b == b'\n').count() as u32 + 1
}

fn snippet_from_span(text: &str, start_idx: usize, end_idx: usize) -> (String, u32, u32) {
    let snippet = text[start_idx..end_idx.min(text.len())].to_string();
    let start_line = line_from_index(text, start_idx);
    let end_line = line_from_index(text, end_idx.saturating_sub(1).max(start_idx));
    (snippet, start_line, end_line)
}

fn extract_file_comment_from_lines(lines: &[&str]) -> String {
    let mut comment_lines: Vec<String> = Vec::new();
    let mut in_block = false;
    for line in lines {
        let stripped = line.trim();
        if in_block {
            comment_lines.push(stripped.to_string());
            if stripped.contains("*/") {
                in_block = false;
            }
            continue;
        }
        if stripped.starts_with("--") || stripped.starts_with("//") {
            comment_lines.push(stripped.to_string());
            continue;
        }
        if stripped.starts_with("/*") {
            comment_lines.push(stripped.to_string());
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
        .into_iter()
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

fn extract_leading_comment_from_lines(lines: &[&str], start_line: u32) -> String {
    let mut comment_lines: Vec<String> = Vec::new();
    let mut in_block = false;
    let mut idx = start_line as i64 - 2;
    while idx >= 0 {
        let line = lines[idx as usize];
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
        .into_iter()
        .rev()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

fn find_definition_end(masked: &str, start_idx: usize) -> usize {
    if let Some(cap) = SQL_END_RE.captures(&masked[start_idx..]) {
        let label = cap
            .name("label")
            .map(|m| m.as_str().to_ascii_lowercase())
            .unwrap_or_default();
        if !SQL_BLOCK_END_LABELS.contains(&label.as_str()) {
            return start_idx + cap.get(0).unwrap().end();
        }
    }
    if let Some(semi) = masked[start_idx..].find(';') {
        return start_idx + semi + 1;
    }
    masked.len()
}

fn find_routine_end(masked: &str, start_idx: usize, name: &str) -> usize {
    let mut candidates: Vec<String> = vec![name.to_string()];
    if let Some((_, leaf)) = name.rsplit_once('.') {
        candidates.push(leaf.to_string());
    }
    for candidate in candidates {
        let pattern = format!(r"(?i)\bend\b\s+{}\s*;", regex::escape(&candidate));
        if let Ok(re) = Regex::new(&pattern) {
            if let Some(m) = re.find(&masked[start_idx..]) {
                return start_idx + m.end();
            }
        }
    }
    find_definition_end(masked, start_idx)
}

fn find_body_start(masked: &str, start_idx: usize, end_idx: usize) -> Option<usize> {
    SQL_BODY_START_RE
        .find(&masked[start_idx..end_idx.min(masked.len())])
        .map(|m| start_idx + m.end())
}

fn extract_paren_segment(text: &str, open_index: usize) -> Option<String> {
    let bytes = text.as_bytes();
    let mut depth: i32 = 0;
    let mut in_string: Option<u8> = None;
    let mut i = open_index;
    while i < bytes.len() {
        let ch = bytes[i];
        if let Some(q) = in_string {
            if ch == q {
                in_string = None;
            } else if ch == b'\\' {
                i += 1;
            }
            i += 1;
            continue;
        }
        if ch == b'\'' || ch == b'"' {
            in_string = Some(ch);
            i += 1;
            continue;
        }
        if ch == b'(' {
            depth += 1;
            i += 1;
            continue;
        }
        if ch == b')' {
            depth -= 1;
            if depth == 0 {
                return Some(text[open_index + 1..i].to_string());
            }
            i += 1;
            continue;
        }
        i += 1;
    }
    None
}

fn count_params_segment(segment: &str) -> u32 {
    if segment.trim().is_empty() {
        return 0;
    }
    let mut depth: i32 = 0;
    let mut in_string: Option<u8> = None;
    let mut count: u32 = 0;
    let mut has_token = false;
    for ch in segment.bytes() {
        if let Some(q) = in_string {
            if ch == q {
                in_string = None;
            } else if ch == b'\\' {
                continue;
            }
            has_token = true;
            continue;
        }
        if ch == b'\'' || ch == b'"' {
            in_string = Some(ch);
            has_token = true;
            continue;
        }
        if ch == b'(' {
            depth += 1;
            has_token = true;
            continue;
        }
        if ch == b')' {
            if depth > 0 {
                depth -= 1;
            }
            has_token = true;
            continue;
        }
        if ch == b',' && depth == 0 {
            if has_token {
                count += 1;
            }
            has_token = false;
            continue;
        }
        if !ch.is_ascii_whitespace() {
            has_token = true;
        }
    }
    if has_token {
        count += 1;
    }
    count
}

fn split_scope(qualified: &str) -> (Option<String>, String) {
    if let Some((scope, name)) = qualified.rsplit_once('.') {
        (Some(scope.trim().to_string()), name.trim().to_string())
    } else {
        (None, qualified.trim().to_string())
    }
}

fn is_valid_callee(simple: &str) -> bool {
    let token = simple.to_ascii_lowercase();
    !token.is_empty() && !SQL_CALL_KEYWORDS.contains(&token.as_str()) && !SQL_TYPE_KEYWORDS.contains(&token.as_str())
}

fn is_builtin_callee(qualified: &str, simple: &str) -> bool {
    let q = qualified.to_ascii_lowercase();
    let s = simple.to_ascii_lowercase();
    if s.is_empty() {
        return true;
    }
    if SQL_CALL_KEYWORDS.contains(&s.as_str()) || SQL_TYPE_KEYWORDS.contains(&s.as_str()) {
        return true;
    }
    SQL_BUILTIN_PREFIXES.iter().any(|p| q.starts_with(p))
}

fn normalize_call_parts(text: &str) -> (String, String) {
    // Strip A.B<> generic type parameters
    let re_gt = Regex::new(r"<[^<>]*>").unwrap();
    let mut cleaned = re_gt.replace_all(text, "").into_owned();
    cleaned = cleaned.replace("?.", ".");
    cleaned = cleaned.replace("::", ".");
    let re_ws = Regex::new(r"\s+").unwrap();
    cleaned = re_ws.replace_all(&cleaned, "").into_owned();
    cleaned = cleaned.trim_matches('.').to_string();
    let simple = if cleaned.is_empty() {
        String::new()
    } else {
        cleaned.rsplit('.').next().unwrap_or("").to_string()
    };
    (cleaned, simple)
}

// ── Public entry point ──────────────────────────────────────────────────

pub fn parse_sql_source(source: &[u8], rel_path: &str) -> Option<SqlParseOutput> {
    let text = std::str::from_utf8(source).ok()?.to_string();
    if text.is_empty() {
        return None;
    }
    let masked = mask_sql_comments(&text);
    let lines: Vec<&str> = text.split_inclusive('\n').map(|s| s.trim_end_matches('\n')).collect();

    let file_comment = extract_file_comment_from_lines(&lines);
    let file_summary = file_comment.clone();
    let file_def = FileDef {
        file_path: rel_path.to_string(),
        start_line: 1,
        end_line: text.bytes().filter(|b| *b == b'\n').count() as u32 + 1,
        code: text.clone(),
        comment: file_comment,
        summary: file_summary,
        note: String::new(),
    };

    let mut functions: Vec<FunctionDef> = Vec::new();
    let mut relations: Vec<RelationEdge> = Vec::new();
    let mut calls: Vec<CallEdge> = Vec::new();

    for cap in SQL_CREATE_RE.captures_iter(&masked) {
        let mut kind = cap
            .name("kind")
            .map(|m| m.as_str().to_ascii_lowercase())
            .unwrap_or_else(|| "function".to_string());
        if kind == "proc" {
            kind = "procedure".to_string();
        }
        let full_name = cap.name("name").map(|m| m.as_str().to_string()).unwrap_or_default();
        let match_start = cap.get(0).unwrap().start();
        let sig_end = cap.get(0).unwrap().end();
        let end_idx = find_routine_end(&masked, sig_end, &full_name);
        let (snippet, def_start_line, def_end_line) = snippet_from_span(&text, match_start, end_idx);
        let comment = extract_leading_comment_from_lines(&lines, def_start_line);
        let summary = comment.clone();
        let (scope_name, name) = split_scope(&full_name);

        let body_start = match find_body_start(&masked, sig_end, end_idx) {
            Some(b) => b,
            None => continue,
        };

        let mut param_segment = String::new();
        let search_limit = body_start;
        if let Some(param_open) = text[match_start..search_limit.min(text.len())].find('(') {
            let abs_open = match_start + param_open;
            if let Some(seg) = extract_paren_segment(&text, abs_open) {
                param_segment = seg;
            }
        }
        let arity = count_params_segment(&param_segment);

        let func_id = crate::symbols::function_symbol_id(
            scope_name.as_deref(),
            &name,
            arity,
            rel_path,
        );
        functions.push(FunctionDef {
            symbol_id: func_id.clone(),
            qualified_name: crate::symbols::qualified_name(scope_name.as_deref(), &name),
            name: name.clone(),
            kind: kind.clone(),
            scope_name: scope_name.clone(),
            file_path: rel_path.to_string(),
            start_byte: match_start as u32,
            end_byte: end_idx as u32,
            start_line: def_start_line,
            end_line: def_end_line,
            arity,
            code: snippet,
            comment,
            summary,
            note: String::new(),
            // FunctionDef doesn't carry exported in the shared struct; SQL
            // sets it implicitly false in the Python dataclass, but the Rust
            // FunctionDef has no field for it. We track it as a property
            // if any consumer needs it.
        });

        if let Some(s) = scope_name.as_ref() {
            relations.push(RelationEdge {
                source_id: crate::symbols::namespace_id(s),
                source_label: "Namespace".to_string(),
                target_id: func_id.clone(),
                target_label: "Function".to_string(),
                rel_type: "CONTAINS".to_string(),
                properties: Default::default(),
            });
        }

        let body_masked = &masked[body_start..end_idx.min(masked.len())];
        let body_start_line = line_from_index(&text, body_start);
        for call in extract_calls_from_body(body_masked, body_start_line, true) {
            calls.push(CallEdge {
                caller_id: func_id.clone(),
                caller_file: rel_path.to_string(),
                caller_scope: scope_name.clone(),
                call_line: call.call_line,
                call_column: 0,
                call_start_byte: 0,
                call_branch_kind: "none".to_string(),
                call_loop_depth: 0,
                call_control_frames_json: "[]".to_string(),
                call_type: "call_expression".to_string(),
                call_arity: 0,
                callee_name: call.callee_simple,
                callee_id: None,
            });
        }
    }

    Some(SqlParseOutput {
        functions,
        calls,
        classes: Vec::new(),
        namespaces: Vec::new(),
        relations,
        file_def,
    })
}

struct ExtractedCall {
    callee_raw: String,
    callee_qualified: String,
    callee_simple: String,
    call_line: u32,
}

fn extract_calls_from_body(
    body_masked: &str,
    base_line: u32,
    include_generic: bool,
) -> Vec<ExtractedCall> {
    let mut results: Vec<ExtractedCall> = Vec::new();
    let mut seen: HashSet<(String, u32)> = HashSet::new();

    let mut append = |raw_name: &str, start_idx: usize, results: &mut Vec<ExtractedCall>| {
        let raw = raw_name.trim();
        let (qualified, simple) = normalize_call_parts(raw);
        if !is_valid_callee(&simple) {
            return;
        }
        let call_line = base_line + body_masked[..start_idx.min(body_masked.len())]
            .bytes()
            .filter(|b| *b == b'\n')
            .count() as u32;
        let key = (qualified.to_ascii_lowercase(), call_line);
        if seen.contains(&key) {
            return;
        }
        seen.insert(key);
        results.push(ExtractedCall {
            callee_raw: raw.to_string(),
            callee_qualified: qualified,
            callee_simple: simple,
            call_line,
        });
    };

    for cap in SQL_CALL_RE.captures_iter(body_masked) {
        if let Some(name) = cap.name("name") {
            append(name.as_str(), name.start(), &mut results);
        }
    }
    for cap in SQL_EXEC_RE.captures_iter(body_masked) {
        if let Some(name) = cap.name("name") {
            append(name.as_str(), name.start(), &mut results);
        }
    }
    if include_generic {
        for cap in SQL_GENERIC_CALL_RE.captures_iter(body_masked) {
            if let Some(name) = cap.name("name") {
                append(name.as_str(), name.start(), &mut results);
            }
        }
    }
    for cap in SQL_BARE_CALL_RE.captures_iter(body_masked) {
        if let Some(name) = cap.name("name") {
            append(name.as_str(), name.start(), &mut results);
        }
    }

    let _ = is_builtin_callee; // helper exposed for future use
    results
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mask_block_comments() {
        let src = "/* hello */\nselect 1;";
        let masked = mask_sql_comments(src);
        assert!(!masked.contains("hello"));
        assert!(masked.contains("select 1"));
    }

    #[test]
    fn mask_line_comments() {
        let src = "-- comment\nselect 1;";
        let masked = mask_sql_comments(src);
        assert!(!masked.contains("comment"));
        assert!(masked.contains("select 1"));
    }

    #[test]
    fn count_params_segment_basic() {
        assert_eq!(count_params_segment(""), 0);
        assert_eq!(count_params_segment("a"), 1);
        assert_eq!(count_params_segment("a, b"), 2);
        assert_eq!(count_params_segment("a, b, c"), 3);
        assert_eq!(count_params_segment("a int, b varchar(20)"), 2);
        assert_eq!(count_params_segment("'a,b', c"), 2);
    }

    #[test]
    fn split_scope_qualified() {
        assert_eq!(split_scope("schema.proc"), (Some("schema".into()), "proc".into()));
        assert_eq!(split_scope("proc"), (None, "proc".into()));
    }

    #[test]
    fn normalize_call_parts_strips_generics_and_whitespace() {
        let (q, s) = normalize_call_parts("schema.foo<int>");
        assert_eq!(s, "foo");
        assert_eq!(q, "schema.foo");
    }

    #[test]
    fn is_valid_callee_rejects_keywords() {
        assert!(!is_valid_callee("select"));
        assert!(!is_valid_callee("int"));
        assert!(is_valid_callee("my_proc"));
    }

    #[test]
    fn parse_extracts_create_procedure() {
        let src = b"CREATE PROCEDURE my_schema.do_thing(x INT, y VARCHAR(50)) AS\nBEGIN\n  CALL helper_proc(1, 2);\n  EXECUTE other_proc;\nEND;";
        let out = parse_sql_source(src, "schema.sql").unwrap();
        let names: Vec<&str> = out.functions.iter().map(|f| f.name.as_str()).collect();
        assert!(names.contains(&"do_thing"));
        let func = out.functions.iter().find(|f| f.name == "do_thing").unwrap();
        assert_eq!(func.arity, 2);
        assert_eq!(func.scope_name.as_deref(), Some("my_schema"));
    }

    #[test]
    fn parse_extracts_create_function_or_replace() {
        let src = b"CREATE OR REPLACE FUNCTION calc(x INT) RETURNS INT AS BEGIN RETURN x + 1; END;";
        let out = parse_sql_source(src, "fn.sql").unwrap();
        let names: Vec<&str> = out.functions.iter().map(|f| f.name.as_str()).collect();
        assert!(names.contains(&"calc"));
        let func = out.functions.iter().find(|f| f.name == "calc").unwrap();
        assert_eq!(func.arity, 1);
        assert_eq!(func.kind, "function");
    }

    #[test]
    fn parse_extracts_call_edges() {
        let src = b"CREATE PROCEDURE proc1 AS BEGIN CALL helper_proc(1); END;";
        let out = parse_sql_source(src, "p.sql").unwrap();
        let callees: Vec<&str> = out.calls.iter().map(|c| c.callee_name.as_str()).collect();
        assert!(callees.contains(&"helper_proc"));
    }

    #[test]
    fn parse_extracts_exec_call() {
        let src = b"CREATE PROCEDURE p1 AS BEGIN EXECUTE p2; END;";
        let out = parse_sql_source(src, "p.sql").unwrap();
        let callees: Vec<&str> = out.calls.iter().map(|c| c.callee_name.as_str()).collect();
        assert!(callees.contains(&"p2"));
    }

    #[test]
    fn parse_filters_keywords_as_calls() {
        let src = b"CREATE PROCEDURE p1 AS BEGIN SELECT 1 FROM dual; END;";
        let out = parse_sql_source(src, "p.sql").unwrap();
        let callees: Vec<&str> = out.calls.iter().map(|c| c.callee_name.as_str()).collect();
        assert!(!callees.contains(&"SELECT"));
        assert!(!callees.contains(&"select"));
        assert!(!callees.contains(&"FROM"));
        assert!(!callees.contains(&"from"));
        assert!(!callees.contains(&"dual"));
    }

    #[test]
    fn parse_returns_empty_for_empty_source() {
        let out = parse_sql_source(b"", "e.sql");
        assert!(out.is_none());
    }
}

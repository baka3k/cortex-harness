//! Port `tools/sql/sql_analyzer.py` — regex routine scan (create
//! procedure/function) + call extraction + LanguageCodeWriter pipeline.
//! Qdrant/embedding/parse-cache/message-scan là plane Python — không port.

use std::collections::BTreeSet;
use std::path::Path;
use std::sync::OnceLock;

use regex::Regex;

use crate::sqlcommon::{
    self, build_note, count_params_segment, extract_calls_from_body,
    extract_file_comment_from_lines, extract_leading_comment_from_lines, find_body_start,
    find_routine_end, snippet_from_span, split_scope, symbol_id, qualified_name, AnalyzerSpec,
    CallExtractors, CallEdge, FunctionDef, ParsedFile,
};

// ── regex constants ─────────────────────────────────────────────────────────

pub const SQL_IDENTIFIER: &str = r#"[A-Za-z_][\w$#]*"#;
const SQL_QUALIFIED_IDENTIFIER: &str =
    r#"(?:[A-Za-z_][\w$#]*\.)*[A-Za-z_][\w$#]*"#;

fn sql_create_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r#"(?i)\bcreate\s+(?:or\s+replace\s+)?(?P<kind>procedure|proc|function)\s+(?P<name>{SQL_QUALIFIED_IDENTIFIER})"#
        ))
        .unwrap()
    })
}

fn sql_call_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r#"(?i)\bcall\s+(?P<name>{SQL_QUALIFIED_IDENTIFIER})"#
        ))
        .unwrap()
    })
}

fn sql_exec_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r#"(?i)\bexec(?:ute)?\s+(?P<name>{SQL_QUALIFIED_IDENTIFIER})"#
        ))
        .unwrap()
    })
}

fn sql_generic_call_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r#"(?i)\b(?P<name>{SQL_QUALIFIED_IDENTIFIER})\s*\("#
        ))
        .unwrap()
    })
}

fn sql_bare_call_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r#"(?im)^\s*(?P<name>{SQL_QUALIFIED_IDENTIFIER})\s*;\s*$"#
        ))
        .unwrap()
    })
}

fn sql_body_start_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\b(as|is|begin)\b").unwrap())
}

pub const SQL_BLOCK_END_LABELS: &[&str] = &["if", "loop", "case", "while", "repeat", "for"];

pub const SQL_CALL_KEYWORDS: &[&str] = &[
    "and", "as", "begin", "by", "case", "create", "declare", "delete", "drop", "else", "elseif",
    "end", "exec", "execute", "from", "function", "group", "having", "if", "insert", "into",
    "join", "left", "limit", "merge", "not", "null", "on", "or", "order", "procedure", "return",
    "right", "select", "set", "then", "truncate", "union", "update", "values", "when", "where",
    "while",
];

pub const SQL_TYPE_KEYWORDS: &[&str] = &[
    "bigint", "binary", "bit", "blob", "bool", "boolean", "char", "date", "datetime", "decimal",
    "double", "float", "int", "integer", "json", "nchar", "numeric", "nvarchar", "real",
    "smallint", "text", "time", "timestamp", "tinyint", "varchar", "xml",
];

pub const SQL_BUILTIN_PREFIXES: &[&str] =
    &["pg_catalog.", "information_schema.", "sys.", "dbms_", "utl_"];

/// `_should_ignore_directory` của sql_analyzer.
pub const SQL_IGNORE_DIRS: &[&str] = &[
    ".git", ".svn", ".hg", ".idea", ".vscode", "backup", "backups", "dumps", "exports", "logs",
    "log", "tmp", "temp", ".tmp", "tmpdir", ".DS_Store", "Thumbs.db", "node_modules", "dist",
    "build", ".cache", "__pycache__",
];

// ── parse ───────────────────────────────────────────────────────────────────

/// `parse_sql_file` — regex path.
pub fn parse_sql_file(path: &Path, root: &Path) -> ParsedFile {
    let rel_path = rel_slash(root, path);
    // open(..., encoding="utf-8", errors="ignore")
    let source = std::fs::read(path)
        .map(|bytes| cortex_analyzer_framework::ts::decode_ignore(&bytes))
        .unwrap_or_default();
    let masked = sqlcommon::mask_comments(&source);
    let lines = crate::pyutil::splitlines(&source);
    let end_line = source.matches('\n').count() as i64 + 1;
    let file_comment = extract_file_comment_from_lines(&lines);
    let file_summary = file_comment.clone();
    let file_note = build_note(&source, &file_comment, &file_summary);
    let file_def = crate::sqlcommon::FileDef {
        file_path: rel_path.clone(),
        start_line: 1,
        end_line,
        code: source.clone(),
        comment: file_comment,
        summary: file_summary,
        note: file_note,
        imports: Vec::new(),
        exports: Vec::new(),
    };

    let mut functions: Vec<FunctionDef> = Vec::new();
    let mut calls: Vec<CallEdge> = Vec::new();
    let extractors = CallExtractors {
        call_re: sql_call_re(),
        exec_re: sql_exec_re(),
        generic_re: sql_generic_call_re(),
        bare_re: sql_bare_call_re(),
        include_generic: true,
    };
    let keywords = sql_keywords();
    let type_keywords = sql_type_keywords();

    for caps in sql_create_re().captures_iter(&masked) {
        let mut kind = caps["kind"].to_lowercase();
        if kind == "proc" {
            kind = "procedure".to_string();
        }
        let full_name = caps["name"].to_string();
        let start_idx = caps.get(0).map(|m| m.start()).unwrap_or(0);
        let match_end = caps.get(0).map(|m| m.end()).unwrap_or(0);
        let end_idx = find_routine_end(&masked, match_end, &full_name, SQL_BLOCK_END_LABELS);
        let (snippet, def_start_line, def_end_line) =
            snippet_from_span(&source, start_idx, end_idx);
        let comment = extract_leading_comment_from_lines(&lines, def_start_line);
        let summary = comment.clone();
        let note = build_note(&snippet, &comment, &summary);
        let (scope_name, name) = split_scope(&full_name);
        let body_start = match find_body_start(sql_body_start_re(), &masked, match_end, end_idx) {
            Some(offset) => offset,
            None => continue,
        };
        let search_limit = body_start;
        let param_open = find_byte_between(&source, match_end, search_limit, b'(');
        let param_segment = param_open
            .and_then(|open| extract_paren_segment_owned(&source, open))
            .unwrap_or_default();
        let arity = count_params_segment(&param_segment);
        let func_id = symbol_id(scope_name.as_deref(), &name, arity, &rel_path);
        functions.push(FunctionDef {
            symbol_id: func_id.clone(),
            qualified_name: qualified_name(scope_name.as_deref(), &name),
            name: name.clone(),
            kind,
            scope_name: scope_name.clone(),
            file_path: rel_path.clone(),
            start_line: def_start_line,
            end_line: def_end_line,
            arity,
            code: snippet,
            comment,
            summary,
            note,
            exported: false,
        });
        let body_masked = &masked[body_start.min(masked.len())..end_idx.min(masked.len())];
        let body_start_line = sqlcommon::line_from_index(&source, body_start);
        for (callee_raw, callee_qualified, callee_simple, call_line) in
            extract_calls_from_body(&extractors, body_masked, &keywords, &type_keywords, SQL_BUILTIN_PREFIXES, body_start_line)
        {
            calls.push(CallEdge {
                caller_id: func_id.clone(),
                caller_scope: scope_name.clone(),
                callee_name: callee_simple.clone(),
                callee_arity: None,
                callee_raw,
                callee_qualified,
                callee_simple,
                call_line,
            });
        }
    }

    ParsedFile {
        functions,
        calls,
        namespaces: Vec::new(),
        relations: Vec::new(),
        file_def,
    }
}

// ── shared helpers (module-private) ─────────────────────────────────────────

pub(crate) fn rel_slash(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .components()
        .map(|c| c.as_os_str().to_string_lossy().to_string())
        .collect::<Vec<_>>()
        .join("/")
}

pub(crate) fn find_byte_between(text: &str, start: usize, end: usize, needle: u8) -> Option<usize> {
    let slice = &text.as_bytes()[start.min(text.len())..end.min(text.len())];
    slice.iter().position(|&b| b == needle).map(|rel| start + rel)
}

/// `_extract_paren_segment` với offset tuyệt đối.
pub(crate) fn extract_paren_segment_owned(text: &str, open_index: usize) -> Option<String> {
    sqlcommon::extract_paren_segment(text, open_index)
}

pub(crate) fn sql_keywords() -> BTreeSet<String> {
    SQL_CALL_KEYWORDS.iter().map(|item| item.to_string()).collect()
}

pub(crate) fn sql_type_keywords() -> BTreeSet<String> {
    SQL_TYPE_KEYWORDS.iter().map(|item| item.to_string()).collect()
}

fn make_extractors() -> CallExtractors<'static> {
    CallExtractors {
        call_re: sql_call_re(),
        exec_re: sql_exec_re(),
        generic_re: sql_generic_call_re(),
        bare_re: sql_bare_call_re(),
        include_generic: true,
    }
}

// ── spec ────────────────────────────────────────────────────────────────────

pub fn analyzer_spec() -> AnalyzerSpec<'static> {
    AnalyzerSpec {
        language_default: "sql",
        ignore_dirs: SQL_IGNORE_DIRS,
        use_common_scan_exclude: false,
        use_extra_ignore: false,
        scan_extensions: &[".sql", ".ddl", ".dml", ".psql"],
        skip_suffixes: &[".bak", ".backup", ".dump", ".gz", ".zip", ".tar", ".swp", ".swo", ".log"],
        skip_names: &[".DS_Store", "Thumbs.db"],
        file_label: "SQL",
        parse_file: parse_sql_file,
        make_extractors,
        keywords: sql_keywords,
        type_keywords: sql_type_keywords,
        builtin_prefixes: SQL_BUILTIN_PREFIXES,
        block_end_labels: SQL_BLOCK_END_LABELS,
    }
}

/// `_get_sql_parser` Grammar SQL (derekstride v0.3.11, vendored) — dùng bởi
/// mybatis sql-semantic gate (đặt ở đây vì `_get_sql_parser` sống trong
/// sql_analyzer).
pub fn new_sql_parser() -> tree_sitter::Parser {
    let mut parser = tree_sitter::Parser::new();
    parser
        .set_language(&sql_grammar_vendored::LANGUAGE.into())
        .expect("Error loading Sql parser");
    parser
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_basic_procedure() {
        let dir = std::env::temp_dir().join(format!("p08_sql_test_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("routines.sql"),
            r#"
-- helper
create procedure audit_log(p_user int)
begin
  insert into audit_log(user_id) values (p_user);
  call enrich_user(p_user);
end;

CREATE OR REPLACE FUNCTION calc_total(x INT)
RETURNS INT
AS $$
  select helper_calc(x);
$$;
"#,
        )
        .unwrap();
        let parsed = parse_sql_file(&dir.join("routines.sql"), &dir);
        assert_eq!(parsed.functions.len(), 2);
        assert_eq!(parsed.functions[0].kind, "procedure");
        assert_eq!(parsed.functions[0].name, "audit_log");
        assert_eq!(parsed.functions[0].arity, 1);
        let callee_names: Vec<&str> =
            parsed.calls.iter().map(|c| c.callee_simple.as_str()).collect();
        assert!(callee_names.contains(&"enrich_user"));
        assert!(callee_names.contains(&"helper_calc"));
        // `insert into` không được nhận làm callee (keyword)
        assert!(!callee_names.contains(&"insert"));
        let _ = std::fs::remove_dir_all(&dir);
    }
}

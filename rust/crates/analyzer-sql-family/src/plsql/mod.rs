//! Port `tools/plsql/plsql_analyzer.py` — packages (spec/body), standalone
//! routines, triggers, dbms_scheduler job blocks; regex scan + call
//! extraction + LanguageCodeWriter pipeline.

use std::collections::{BTreeSet, HashSet};
use std::path::Path;
use std::sync::OnceLock;

use regex::Regex;

use crate::sqlcommon::{
    self, build_note, count_params_segment, extract_calls_from_body,
    extract_file_comment_from_lines, extract_leading_comment_from_lines, find_body_start,
    find_routine_end, snippet_from_span, split_scope, symbol_id, qualified_name, AnalyzerSpec,
    CallExtractors, CallEdge, FunctionDef, NamespaceDef, ParsedFile, RelationEdge,
};

fn plsql_identifier() -> &'static str {
    r#"[A-Za-z_][\w$#]*"#
}

fn plsql_qualified_identifier() -> &'static str {
    r#"(?:[A-Za-z_][\w$#]*\.)*[A-Za-z_][\w$#]*"#
}

fn plsql_create_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r#"(?i)\bcreate\s+(?:or\s+replace\s+)?(?P<kind>procedure|proc|function)\s+(?P<name>{})"#,
            plsql_qualified_identifier()
        ))
        .unwrap()
    })
}

fn plsql_package_body_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r#"(?i)\bcreate\s+(?:or\s+replace\s+)?package\s+body\s+(?P<name>{})"#,
            plsql_qualified_identifier()
        ))
        .unwrap()
    })
}

fn plsql_package_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r#"(?i)\bcreate\s+(?:or\s+replace\s+)?package\s+(?P<name>{})"#,
            plsql_qualified_identifier()
        ))
        .unwrap()
    })
}

fn plsql_trigger_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r#"(?i)\bcreate\s+(?:or\s+replace\s+)?trigger\s+(?P<name>{})"#,
            plsql_qualified_identifier()
        ))
        .unwrap()
    })
}

fn plsql_proc_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r#"(?i)\bprocedure\s+(?P<name>{})"#,
            plsql_identifier()
        ))
        .unwrap()
    })
}

fn plsql_func_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r#"(?i)\bfunction\s+(?P<name>{})"#,
            plsql_identifier()
        ))
        .unwrap()
    })
}

fn plsql_call_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r#"(?i)\bcall\s+(?P<name>{})"#,
            plsql_qualified_identifier()
        ))
        .unwrap()
    })
}

fn plsql_exec_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r#"(?i)\bexec(?:ute)?\s+(?P<name>{})"#,
            plsql_qualified_identifier()
        ))
        .unwrap()
    })
}

fn plsql_generic_call_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r#"(?i)\b(?P<name>{})\s*\("#,
            plsql_qualified_identifier()
        ))
        .unwrap()
    })
}

fn plsql_bare_call_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r#"(?im)^\s*(?P<name>{})\s*;\s*$"#,
            plsql_qualified_identifier()
        ))
        .unwrap()
    })
}

fn plsql_body_start_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\b(as|is|begin)\b").unwrap())
}

fn plsql_job_call_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\bdbms_scheduler\s*\.\s*create_job\s*\(").unwrap())
}

fn plsql_job_name_arg_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r#"(?is)\bjob_name\s*=>\s*'(?P<value>(?:''|[^'])*)'"#).unwrap()
    })
}

fn plsql_job_action_arg_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r#"(?is)\bjob_action\s*=>\s*'(?P<value>(?:''|[^'])*)'"#).unwrap()
    })
}

pub const PLSQL_BLOCK_END_LABELS: &[&str] = &["if", "loop", "case", "while", "repeat", "for"];

pub const PLSQL_CALL_KEYWORDS: &[&str] = &[
    "and", "begin", "between", "bulk", "case", "close", "collect", "commit", "create", "cursor",
    "declare", "delete", "drop", "else", "elsif", "end", "execute", "exit", "exception", "fetch",
    "for", "forall", "from", "function", "group", "having", "if", "in", "insert", "into", "is",
    "join", "loop", "merge", "not", "null", "open", "or", "order", "package", "procedure",
    "raise", "return", "rollback", "select", "then", "type", "update", "values", "when", "where",
    "while",
];

pub const PLSQL_TYPE_KEYWORDS: &[&str] = &[
    "varchar2", "varchar", "nvarchar2", "char", "nchar", "number", "integer", "pls_integer",
    "binary_integer", "float", "binary_float", "binary_double", "boolean", "date", "timestamp",
    "clob", "blob", "xmltype", "raw", "long",
];

pub const PLSQL_BUILTIN_PREFIXES: &[&str] = &[
    "dbms_", "utl_", "sys.", "sys_", "apex_", "owa_", "htp.", "htf.",
];

/// `_should_ignore_directory` của plsql_analyzer (union COMMON_SCAN_EXCLUDE).
pub const PLSQL_IGNORE_DIRS: &[&str] = &[
    ".git", ".svn", ".hg", ".idea", ".vscode", "output", "logs", ".sql_history", "log", "backup",
    "backups", "exports", "dumps", "tmp", "temp", ".tmp", "tmpdir", ".DS_Store", "Thumbs.db",
    "node_modules", "dist", "build", ".cache", "__pycache__", "dpump", "datapump",
];

// ── parse ───────────────────────────────────────────────────────────────────

/// `_find_package_ranges`.
fn find_package_ranges(masked_text: &str) -> Vec<(usize, usize, String)> {
    let mut ranges: Vec<(usize, usize, String)> = Vec::new();
    let mut seen_names: HashSet<String> = HashSet::new();
    for caps in plsql_package_body_re().captures_iter(masked_text) {
        let name = caps["name"].to_string();
        let start_idx = caps.get(0).map(|m| m.start()).unwrap_or(0);
        let end_idx = find_package_end(masked_text, caps.get(0).map(|m| m.end()).unwrap_or(0), &name);
        seen_names.insert(name.to_lowercase());
        ranges.push((start_idx, end_idx, name));
    }
    for caps in plsql_package_re().captures_iter(masked_text) {
        let name = caps["name"].to_string();
        if seen_names.contains(&name.to_lowercase()) {
            continue;
        }
        let start_idx = caps.get(0).map(|m| m.start()).unwrap_or(0);
        let end_idx = find_package_end(masked_text, caps.get(0).map(|m| m.end()).unwrap_or(0), &name);
        ranges.push((start_idx, end_idx, name));
    }
    ranges
}

/// `_find_package_end`.
fn find_package_end(masked_text: &str, start_idx: usize, package_name: &str) -> usize {
    let short_name = package_name.rsplit('.').next().unwrap_or(package_name);
    let slice = &masked_text[start_idx.min(masked_text.len())..];
    for candidate in [package_name, short_name] {
        let pattern = Regex::new(&format!(
            r#"(?i)\bend\b\s+{}\s*;"#,
            crate::pyutil::re_escape(candidate)
        ))
        .unwrap();
        if let Some(m) = pattern.find(slice) {
            return start_idx + m.end();
        }
    }
    sqlcommon::find_definition_end(masked_text, start_idx, PLSQL_BLOCK_END_LABELS)
}

/// `_extract_job_blocks`.
fn extract_job_blocks(
    source: &str,
    masked: &str,
    rel_path: &str,
    keywords: &BTreeSet<String>,
    type_keywords: &BTreeSet<String>,
) -> (Vec<FunctionDef>, Vec<CallEdge>) {
    let mut functions: Vec<FunctionDef> = Vec::new();
    let mut calls: Vec<CallEdge> = Vec::new();
    let mut seen_ids: HashSet<String> = HashSet::new();
    let extractors = CallExtractors {
        call_re: plsql_call_re(),
        exec_re: plsql_exec_re(),
        generic_re: plsql_generic_call_re(),
        bare_re: plsql_bare_call_re(),
        include_generic: true,
    };
    for caps in plsql_job_call_re().captures_iter(masked) {
        let match_start = caps.get(0).map(|m| m.start()).unwrap_or(0);
        let match_end = caps.get(0).map(|m| m.end()).unwrap_or(0);
        let open_idx = match crate::sqlfile::find_byte_between(source, match_start, match_end + 2, b'(') {
            Some(idx) => idx,
            None => continue,
        };
        let close_idx = match sqlcommon::find_matching_paren(source, open_idx) {
            Some(idx) => idx,
            None => continue,
        };
        let args_text = &source[open_idx + 1..close_idx];
        let action_match = match plsql_job_action_arg_re().captures(args_text) {
            Some(m) => m,
            None => continue,
        };
        let action_sql = action_match["value"].replace("''", "'");
        if action_sql.trim().is_empty() {
            continue;
        }
        let raw_name = plsql_job_name_arg_re()
            .captures(args_text)
            .map(|m| m["value"].replace("''", "'").trim().to_string())
            .unwrap_or_default();
        let match_line = sqlcommon::line_from_index(source, match_start);
        let job_name = if raw_name.is_empty() {
            format!("job_block_{match_line}")
        } else {
            raw_name
        };
        let scope_name = "DBMS_SCHEDULER".to_string();
        let mut symbol = symbol_id(Some(&scope_name), &job_name, 0, rel_path);
        if seen_ids.contains(&symbol) {
            symbol = symbol_id(
                Some(&scope_name),
                &format!("{job_name}_{match_line}"),
                0,
                rel_path,
            );
        }
        seen_ids.insert(symbol.clone());
        let start_line = sqlcommon::line_from_index(source, match_start);
        let end_line = sqlcommon::line_from_index(source, close_idx);
        let comment = extract_leading_comment_from_lines(&crate::pyutil::splitlines(source), start_line);
        let summary = comment.clone();
        let note = build_note(&action_sql, &comment, &summary);
        functions.push(FunctionDef {
            symbol_id: symbol.clone(),
            qualified_name: qualified_name(Some(&scope_name), &job_name),
            name: job_name,
            kind: "job_block".to_string(),
            scope_name: Some(scope_name.clone()),
            file_path: rel_path.to_string(),
            start_line,
            end_line,
            arity: 0,
            code: action_sql.clone(),
            comment,
            summary,
            note,
            exported: false,
        });
        let action_masked = sqlcommon::mask_comments(&action_sql);
        for (callee_raw, callee_qualified, callee_simple, call_line) in extract_calls_from_body(
            &extractors,
            &action_masked,
            keywords,
            type_keywords,
            PLSQL_BUILTIN_PREFIXES,
            start_line,
        ) {
            calls.push(CallEdge {
                caller_id: symbol.clone(),
                caller_scope: Some(scope_name.clone()),
                callee_name: callee_simple.clone(),
                callee_arity: None,
                callee_raw,
                callee_qualified,
                callee_simple,
                call_line,
            });
        }
    }
    (functions, calls)
}

/// `parse_plsql_file`.
pub fn parse_plsql_file(path: &Path, root: &Path) -> ParsedFile {
    let rel_path = crate::sqlfile::rel_slash(root, path);
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

    let mut namespaces: Vec<NamespaceDef> = Vec::new();
    let mut functions: Vec<FunctionDef> = Vec::new();
    let mut relations: Vec<RelationEdge> = Vec::new();
    let mut calls: Vec<CallEdge> = Vec::new();
    let mut namespace_registry: std::collections::HashMap<String, usize> =
        std::collections::HashMap::new();
    let mut function_ids: HashSet<String> = HashSet::new();
    let mut relation_keys: HashSet<(String, String, String)> = HashSet::new();

    let extractors = CallExtractors {
        call_re: plsql_call_re(),
        exec_re: plsql_exec_re(),
        generic_re: plsql_generic_call_re(),
        bare_re: plsql_bare_call_re(),
        include_generic: true,
    };
    let keywords = plsql_keywords();
    let type_keywords = plsql_type_keywords();

    let add_function = |functions: &mut Vec<FunctionDef>,
                            item: FunctionDef,
                            function_ids: &mut HashSet<String>|
     -> bool {
        if function_ids.contains(&item.symbol_id) {
            return false;
        }
        function_ids.insert(item.symbol_id.clone());
        functions.push(item);
        true
    };
    let add_relation = |relations: &mut Vec<RelationEdge>,
                            item: RelationEdge,
                            relation_keys: &mut HashSet<(String, String, String)>| {
        let key = (item.source_id.clone(), item.target_id.clone(), item.rel_type.clone());
        if relation_keys.contains(&key) {
            return;
        }
        relation_keys.insert(key);
        relations.push(item);
    };

    // ── packages ────────────────────────────────────────────────────────────
    for (start_idx, end_idx, pkg_name_raw) in find_package_ranges(&masked) {
        let pkg_name = pkg_name_raw.trim().to_string();
        let pkg_scope = pkg_name.clone();
        let (snippet, ns_start_line, ns_end_line) =
            snippet_from_span(&source, start_idx, end_idx);
        let comment = extract_leading_comment_from_lines(&lines, ns_start_line);
        let summary = comment.clone();
        let note = build_note(&snippet, &comment, &summary);
        let namespace_id = sqlcommon::namespace_id(&pkg_name);
        if !namespace_registry.contains_key(&namespace_id) {
            namespaces.push(NamespaceDef {
                symbol_id: namespace_id.clone(),
                qualified_name: pkg_name.clone(),
                name: pkg_name.clone(),
                file_path: rel_path.clone(),
                start_line: ns_start_line,
                end_line: ns_end_line,
                code: snippet,
                comment,
                summary,
                note,
            });
            namespace_registry.insert(namespace_id.clone(), namespaces.len() - 1);
        }

        let segment_masked = &masked[start_idx.min(masked.len())..end_idx.min(masked.len())];
        for matcher in [plsql_proc_re(), plsql_func_re()] {
            for caps in matcher.captures_iter(segment_masked) {
                let local_start = caps.get(0).map(|m| m.start()).unwrap_or(0);
                let abs_start = start_idx + local_start;
                let name = caps["name"].to_string();
                let before = &segment_masked
                    [local_start.saturating_sub(5)..local_start];
                if before.to_lowercase().ends_with("end ") {
                    continue;
                }
                let routine_end =
                    find_routine_end(&masked, abs_start, &name, PLSQL_BLOCK_END_LABELS);
                if routine_end <= abs_start {
                    continue;
                }
                let routine_end = routine_end.min(end_idx);
                let body_start = match find_body_start(
                    plsql_body_start_re(),
                    &masked,
                    abs_start,
                    routine_end,
                ) {
                    Some(offset) => offset,
                    None => continue,
                };
                let (snippet, def_start_line, def_end_line) =
                    snippet_from_span(&source, abs_start, routine_end);
                let comment = extract_leading_comment_from_lines(&lines, def_start_line);
                let summary = comment.clone();
                let note = build_note(&snippet, &comment, &summary);
                let param_open =
                    crate::sqlfile::find_byte_between(&source, abs_start, body_start, b'(');
                let param_segment = param_open
                    .and_then(|open| crate::sqlfile::extract_paren_segment_owned(&source, open))
                    .unwrap_or_default();
                let arity = count_params_segment(&param_segment);
                let func_id = symbol_id(Some(&pkg_scope), &name, arity, &rel_path);
                let kind = if std::ptr::eq(matcher, plsql_proc_re()) {
                    "procedure"
                } else {
                    "function"
                };
                let item = FunctionDef {
                    symbol_id: func_id.clone(),
                    qualified_name: qualified_name(Some(&pkg_scope), &name),
                    name: name.clone(),
                    kind: kind.to_string(),
                    scope_name: Some(pkg_scope.clone()),
                    file_path: rel_path.clone(),
                    start_line: def_start_line,
                    end_line: def_end_line,
                    arity,
                    code: snippet,
                    comment,
                    summary,
                    note,
                    exported: false,
                };
                if !add_function(&mut functions, item, &mut function_ids) {
                    continue;
                }
                add_relation(
                    &mut relations,
                    RelationEdge {
                        source_id: namespace_id.clone(),
                        source_label: "Namespace".to_string(),
                        target_id: func_id.clone(),
                        target_label: "Function".to_string(),
                        rel_type: "CONTAINS".to_string(),
                    },
                    &mut relation_keys,
                );
                let body_masked =
                    &masked[body_start.min(masked.len())..routine_end.min(masked.len())];
                let body_start_line = sqlcommon::line_from_index(&source, body_start);
                for (callee_raw, callee_qualified, callee_simple, call_line) in
                    extract_calls_from_body(
                        &extractors,
                        body_masked,
                        &keywords,
                        &type_keywords,
                        PLSQL_BUILTIN_PREFIXES,
                        body_start_line,
                    )
                {
                    calls.push(CallEdge {
                        caller_id: func_id.clone(),
                        caller_scope: Some(pkg_scope.clone()),
                        callee_name: callee_simple.clone(),
                        callee_arity: None,
                        callee_raw,
                        callee_qualified,
                        callee_simple,
                        call_line,
                    });
                }
            }
        }
    }

    // ── standalone routines ─────────────────────────────────────────────────
    for caps in plsql_create_re().captures_iter(&masked) {
        let mut kind = caps["kind"].to_lowercase();
        if kind == "proc" {
            kind = "procedure".to_string();
        }
        let full_name = caps["name"].to_string();
        let start_idx = caps.get(0).map(|m| m.start()).unwrap_or(0);
        let match_end = caps.get(0).map(|m| m.end()).unwrap_or(0);
        let end_idx = find_routine_end(&masked, match_end, &full_name, PLSQL_BLOCK_END_LABELS);
        let (snippet, def_start_line, def_end_line) =
            snippet_from_span(&source, start_idx, end_idx);
        let comment = extract_leading_comment_from_lines(&lines, def_start_line);
        let summary = comment.clone();
        let note = build_note(&snippet, &comment, &summary);
        let (scope_name, name) = split_scope(&full_name);
        let body_start = match find_body_start(plsql_body_start_re(), &masked, match_end, end_idx) {
            Some(offset) => offset,
            None => continue,
        };
        let param_open = crate::sqlfile::find_byte_between(&source, match_end, body_start, b'(');
        let param_segment = param_open
            .and_then(|open| crate::sqlfile::extract_paren_segment_owned(&source, open))
            .unwrap_or_default();
        let arity = count_params_segment(&param_segment);
        let func_id = symbol_id(scope_name.as_deref(), &name, arity, &rel_path);
        let item = FunctionDef {
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
        };
        if !add_function(&mut functions, item, &mut function_ids) {
            continue;
        }
        let body_masked = &masked[body_start.min(masked.len())..end_idx.min(masked.len())];
        let body_start_line = sqlcommon::line_from_index(&source, body_start);
        for (callee_raw, callee_qualified, callee_simple, call_line) in extract_calls_from_body(
            &extractors,
            body_masked,
            &keywords,
            &type_keywords,
            PLSQL_BUILTIN_PREFIXES,
            body_start_line,
        ) {
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

    // ── triggers ────────────────────────────────────────────────────────────
    for caps in plsql_trigger_re().captures_iter(&masked) {
        let full_name = caps["name"].to_string();
        let (scope_name, name) = split_scope(&full_name);
        let start_idx = caps.get(0).map(|m| m.start()).unwrap_or(0);
        let match_end = caps.get(0).map(|m| m.end()).unwrap_or(0);
        let end_idx = find_routine_end(&masked, match_end, &full_name, PLSQL_BLOCK_END_LABELS);
        let body_start = match find_body_start(plsql_body_start_re(), &masked, match_end, end_idx) {
            Some(offset) => offset,
            None => continue,
        };
        let (snippet, def_start_line, def_end_line) =
            snippet_from_span(&source, start_idx, end_idx);
        let comment = extract_leading_comment_from_lines(&lines, def_start_line);
        let summary = comment.clone();
        let note = build_note(&snippet, &comment, &summary);
        let func_id = symbol_id(scope_name.as_deref(), &name, 0, &rel_path);
        let item = FunctionDef {
            symbol_id: func_id.clone(),
            qualified_name: qualified_name(scope_name.as_deref(), &name),
            name: name.clone(),
            kind: "trigger".to_string(),
            scope_name: scope_name.clone(),
            file_path: rel_path.clone(),
            start_line: def_start_line,
            end_line: def_end_line,
            arity: 0,
            code: snippet,
            comment,
            summary,
            note,
            exported: false,
        };
        if !add_function(&mut functions, item, &mut function_ids) {
            continue;
        }
        let body_masked = &masked[body_start.min(masked.len())..end_idx.min(masked.len())];
        let body_start_line = sqlcommon::line_from_index(&source, body_start);
        for (callee_raw, callee_qualified, callee_simple, call_line) in extract_calls_from_body(
            &extractors,
            body_masked,
            &keywords,
            &type_keywords,
            PLSQL_BUILTIN_PREFIXES,
            body_start_line,
        ) {
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

    // ── job blocks ──────────────────────────────────────────────────────────
    let (job_functions, job_calls) =
        extract_job_blocks(&source, &masked, &rel_path, &keywords, &type_keywords);
    for func in job_functions {
        add_function(&mut functions, func, &mut function_ids);
    }
    calls.extend(job_calls);

    ParsedFile {
        functions,
        calls,
        namespaces,
        relations,
        file_def,
    }
}

pub(crate) fn plsql_keywords() -> BTreeSet<String> {
    PLSQL_CALL_KEYWORDS
        .iter()
        .map(|item| item.to_string())
        .collect()
}

pub(crate) fn plsql_type_keywords() -> BTreeSet<String> {
    PLSQL_TYPE_KEYWORDS
        .iter()
        .map(|item| item.to_string())
        .collect()
}

fn make_extractors() -> CallExtractors<'static> {
    CallExtractors {
        call_re: plsql_call_re(),
        exec_re: plsql_exec_re(),
        generic_re: plsql_generic_call_re(),
        bare_re: plsql_bare_call_re(),
        include_generic: true,
    }
}

pub fn analyzer_spec() -> AnalyzerSpec<'static> {
    AnalyzerSpec {
        language_default: "plsql",
        ignore_dirs: PLSQL_IGNORE_DIRS,
        use_common_scan_exclude: true,
        use_extra_ignore: true,
        scan_extensions: &[
            ".sql", ".pls", ".plsql", ".pks", ".pkb", ".pkg", ".pck", ".spc", ".spb", ".trg",
            ".fnc",
        ],
        skip_suffixes: &[
            ".plb", ".bak", ".backup", ".dmp", ".exp", ".gz", ".swp", ".swo", ".log",
        ],
        skip_names: &[".DS_Store", "Thumbs.db", ".sql_history"],
        file_label: "PL/SQL",
        parse_file: parse_plsql_file,
        make_extractors,
        keywords: plsql_keywords,
        type_keywords: plsql_type_keywords,
        builtin_prefixes: PLSQL_BUILTIN_PREFIXES,
        block_end_labels: PLSQL_BLOCK_END_LABELS,
    }
}

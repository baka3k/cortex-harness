//! Port `tools/servlet_jsp/el_parser.py` — EL tokenizer + references.

use std::collections::BTreeSet;

use crate::servlet_jsp::models::{Diagnostic, ResourceBudgets, SourceSpan};

const KEYWORDS: [&str; 16] = [
    "and", "div", "empty", "eq", "false", "ge", "gt", "instanceof", "le", "lt", "mod", "ne", "not", "null", "or",
    "true",
];

fn implicit_scope_of(name: &str) -> &'static str {
    match name {
        "param" | "paramValues" => "parameter",
        "requestScope" => "request",
        "sessionScope" => "session",
        "applicationScope" => "application",
        "cookie" => "cookie",
        "header" | "headerValues" => "header",
        _ => "",
    }
}

#[derive(Debug, Clone)]
pub struct ElToken {
    pub kind: String,
    pub value: String,
    pub start_offset: i64,
    pub end_offset: i64,
}

#[derive(Debug, Clone)]
pub struct ElFunctionReference {
    pub prefix: String,
    pub name: String,
    pub raw: String,
    pub start_offset: i64,
    pub end_offset: i64,
}

#[derive(Debug, Clone)]
pub struct ElReference {
    pub root: String,
    pub path: Vec<String>,
    pub raw: String,
    pub start_offset: i64,
    pub end_offset: i64,
    pub resolution_status: String,
    pub implicit_scope: String,
}

impl ElReference {
    pub fn property_path(&self) -> String {
        let mut value = self.root.clone();
        for part in &self.path {
            if part.starts_with('[') {
                value.push_str(part);
            } else {
                value.push('.');
                value.push_str(part);
            }
        }
        value
    }
}

#[derive(Debug, Clone)]
pub struct ElStateRead {
    pub implicit_object: String,
    pub scope: String,
    pub name: String,
    pub raw: String,
    pub dynamic: bool,
    pub start_offset: i64,
    pub end_offset: i64,
}

#[derive(Debug, Clone, Default)]
pub struct ElParseResult {
    pub raw: String,
    pub body: String,
    pub span: Option<SourceSpan>,
    pub tokens: Vec<ElToken>,
    pub references: Vec<ElReference>,
    pub functions: Vec<ElFunctionReference>,
    pub state_reads: Vec<ElStateRead>,
    pub diagnostics: Vec<Diagnostic>,
    pub truncated: bool,
    pub complete: bool,
}

impl ElParseResult {
    pub fn variables(&self) -> Vec<String> {
        let mut seen: BTreeSet<String> = BTreeSet::new();
        let mut out: Vec<String> = Vec::new();
        for item in &self.references {
            if seen.insert(item.root.clone()) {
                out.push(item.root.clone());
            }
        }
        out
    }

    pub fn property_paths(&self) -> Vec<String> {
        let mut seen: BTreeSet<String> = BTreeSet::new();
        let mut out: Vec<String> = Vec::new();
        for item in &self.references {
            let path = item.property_path();
            if seen.insert(path.clone()) {
                out.push(path);
            }
        }
        out
    }

    pub fn implicit_objects(&self) -> Vec<String> {
        let mut seen: BTreeSet<String> = BTreeSet::new();
        let mut out: Vec<String> = Vec::new();
        for item in &self.state_reads {
            if seen.insert(item.implicit_object.clone()) {
                out.push(item.implicit_object.clone());
            }
        }
        out
    }
}

pub fn parse_el_expression(
    expression: &str,
    file_path: &str,
    start_line: i64,
    start_column: i64,
    budgets: &ResourceBudgets,
) -> ElParseResult {
    let raw = expression.to_string();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let mut truncated = false;
    let scanned = raw.clone();
    if raw.len() > budgets.max_el_bytes as usize {
        truncated = true;
        diagnostics.push(Diagnostic::new(
            "servlet_jsp.el.byte_budget",
            &format!("EL byte budget {} reached", budgets.max_el_bytes),
            "warning",
            file_path,
            start_line,
            start_line,
        ));
    }

    let mut body_offset = 0i64;
    let mut complete = true;
    let body = if scanned.starts_with("${") || scanned.starts_with("#{") {
        body_offset = 2;
        if scanned.ends_with('}') {
            scanned[2..scanned.len() - 1].to_string()
        } else {
            complete = false;
            diagnostics.push(Diagnostic::new(
                "servlet_jsp.el.unclosed",
                "Unclosed EL expression",
                "warning",
                file_path,
                start_line,
                start_line,
            ));
            scanned[2..].to_string()
        }
    } else {
        scanned.clone()
    };

    let (tokens, token_diagnostics, token_truncated, token_complete) = lex(
        &body,
        body_offset,
        file_path,
        start_line,
        budgets.max_el_tokens as usize,
        budgets.max_el_nesting as usize,
    );
    diagnostics.extend(token_diagnostics);
    let truncated = truncated || token_truncated;
    let (functions, function_token_indexes) = function_references(&tokens, &scanned);
    let references = references(&tokens, &scanned, &function_token_indexes);
    let state_reads = state_reads(&references);
    let span = SourceSpan {
        file_path: file_path.to_string(),
        start_line,
        end_line: start_line + raw.matches('\n').count() as i64,
        start_column,
        end_column: if !raw.contains('\n') {
            start_column + raw.chars().count() as i64
        } else {
            1
        },
    };
    let complete = complete && token_complete && !truncated;
    ElParseResult {
        raw,
        body,
        span: Some(span),
        tokens,
        references,
        functions,
        state_reads,
        diagnostics,
        truncated,
        complete,
    }
}

fn lex(
    body: &str,
    body_offset: i64,
    file_path: &str,
    start_line: i64,
    max_tokens: usize,
    max_nesting: usize,
) -> (Vec<ElToken>, Vec<Diagnostic>, bool, bool) {
    let mut tokens: Vec<ElToken> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let mut truncated = false;
    let mut complete = true;
    let chars: Vec<char> = body.chars().collect();
    let mut index = 0usize;
    let mut nesting: Vec<char> = Vec::new();
    while index < chars.len() {
        let ch = chars[index];
        if ch.is_whitespace() {
            index += 1;
            continue;
        }
        let rest: String = chars[index..(index + 2).min(chars.len())].iter().collect();
        if rest == "//" {
            // line comment
            let mut cursor = index + 2;
            while cursor < chars.len() && chars[cursor] != '\n' {
                cursor += 1;
            }
            index = if cursor >= chars.len() { chars.len() } else { cursor + 1 };
            continue;
        }
        if rest == "/*" {
            let mut cursor = index + 2;
            let mut found = false;
            while cursor + 1 < chars.len() {
                if chars[cursor] == '*' && chars[cursor + 1] == '/' {
                    found = true;
                    break;
                }
                cursor += 1;
            }
            if !found {
                complete = false;
                diagnostics.push(Diagnostic::new(
                    "servlet_jsp.el.unclosed_comment",
                    "Unclosed comment in EL expression",
                    "warning",
                    file_path,
                    start_line,
                    start_line,
                ));
                break;
            }
            index = cursor + 2;
            continue;
        }
        let start = index;
        let kind;
        if ch == '\'' || ch == '"' {
            let quote = ch;
            index += 1;
            let mut escaped = false;
            let mut closed = false;
            while index < chars.len() {
                let current = chars[index];
                index += 1;
                if escaped {
                    escaped = false;
                } else if current == '\\' {
                    escaped = true;
                } else if current == quote {
                    closed = true;
                    break;
                }
            }
            if !closed {
                complete = false;
                diagnostics.push(Diagnostic::new(
                    "servlet_jsp.el.unclosed_string",
                    "Unclosed string literal in EL expression",
                    "warning",
                    file_path,
                    start_line,
                    start_line,
                ));
            }
            kind = "string";
        } else if ch.is_alphabetic() || ch == '_' || ch == '$' {
            index += 1;
            while index < chars.len() && (chars[index].is_alphanumeric() || chars[index] == '_' || chars[index] == '$') {
                index += 1;
            }
            kind = "identifier";
        } else if ch.is_ascii_digit() {
            index += 1;
            while index < chars.len()
                && (chars[index].is_alphanumeric() || chars[index] == '.' || chars[index] == '_')
            {
                index += 1;
            }
            kind = "number";
        } else {
            let pair: String = chars[index..(index + 2).min(chars.len())].iter().collect();
            if ["==", "!=", "<=", ">=", "&&", "||", "->", "+=", "-=", "*=", "/=", "?."].contains(&pair.as_str()) {
                index += 2;
            } else {
                index += 1;
            }
            kind = if "()[]{}.,:?".contains(ch) { "punctuation" } else { "operator" };
        }
        let value: String = chars[start..index].iter().collect();
        tokens.push(ElToken {
            kind: kind.to_string(),
            value: value.clone(),
            start_offset: start as i64 + body_offset,
            end_offset: index as i64 + body_offset,
        });
        if value == "(" || value == "[" || value == "{" {
            nesting.push(value.chars().next().unwrap());
            if nesting.len() > max_nesting {
                diagnostics.push(Diagnostic::new(
                    "servlet_jsp.el.nesting_budget",
                    &format!("EL nesting budget {max_nesting} reached"),
                    "warning",
                    file_path,
                    start_line,
                    start_line,
                ));
                truncated = true;
                break;
            }
        } else if value == ")" || value == "]" || value == "}" {
            let expected = match value.as_str() {
                ")" => '(',
                "]" => '[',
                _ => '{',
            };
            if nesting.is_empty() || *nesting.last().unwrap() != expected {
                complete = false;
                diagnostics.push(Diagnostic::new(
                    "servlet_jsp.el.unexpected_closer",
                    &format!("Unexpected closing token {value:?} in EL expression"),
                    "warning",
                    file_path,
                    start_line,
                    start_line,
                ));
            } else {
                nesting.pop();
            }
        }
        if tokens.len() >= max_tokens {
            if index < chars.len() {
                diagnostics.push(Diagnostic::new(
                    "servlet_jsp.el.token_budget",
                    &format!("EL token budget {max_tokens} reached"),
                    "warning",
                    file_path,
                    start_line,
                    start_line,
                ));
                truncated = true;
            }
            break;
        }
    }
    if !nesting.is_empty() && !truncated {
        complete = false;
        diagnostics.push(Diagnostic::new(
            "servlet_jsp.el.unbalanced_nesting",
            "Unbalanced grouping token in EL expression",
            "warning",
            file_path,
            start_line,
            start_line,
        ));
    }
    (tokens, diagnostics, truncated, complete)
}

/// Slice chuỗi theo CHAR index (khớp slicing code-point của Python).
fn char_slice(text: &str, start: i64, end: i64) -> String {
    text.chars()
        .skip(start.max(0) as usize)
        .take((end - start).max(0) as usize)
        .collect()
}

fn function_references(tokens: &[ElToken], raw: &str) -> (Vec<ElFunctionReference>, BTreeSet<usize>) {
    let mut functions: Vec<ElFunctionReference> = Vec::new();
    let mut indexes: BTreeSet<usize> = BTreeSet::new();
    if tokens.len() < 4 {
        return (functions, indexes);
    }
    for index in 0..tokens.len().saturating_sub(3) {
        let prefix = &tokens[index];
        let colon = &tokens[index + 1];
        let name = &tokens[index + 2];
        let opening = &tokens[index + 3];
        if prefix.kind == "identifier" && colon.value == ":" && name.kind == "identifier" && opening.value == "(" {
            indexes.insert(index);
            indexes.insert(index + 2);
            functions.push(ElFunctionReference {
                prefix: prefix.value.clone(),
                name: name.value.clone(),
                raw: char_slice(raw, prefix.start_offset, name.end_offset),
                start_offset: prefix.start_offset,
                end_offset: name.end_offset,
            });
        }
    }
    (functions, indexes)
}

fn references(tokens: &[ElToken], raw: &str, function_token_indexes: &BTreeSet<usize>) -> Vec<ElReference> {
    let mut references: Vec<ElReference> = Vec::new();
    for index in 0..tokens.len() {
        let token = &tokens[index];
        if token.kind != "identifier" || KEYWORDS.contains(&token.value.as_str()) || function_token_indexes.contains(&index)
        {
            continue;
        }
        let previous = if index > 0 { tokens[index - 1].value.clone() } else { String::new() };
        if previous == "." || previous == "?." || previous == ":" {
            continue;
        }
        let mut path: Vec<String> = Vec::new();
        let mut end_offset = token.end_offset;
        let mut cursor = index + 1;
        let mut dynamic = false;
        while cursor < tokens.len() {
            let marker = &tokens[cursor];
            if (marker.value == "." || marker.value == "?.")
                && cursor + 1 < tokens.len()
                && tokens[cursor + 1].kind == "identifier"
            {
                let part = &tokens[cursor + 1];
                path.push(part.value.clone());
                end_offset = part.end_offset;
                cursor += 2;
                continue;
            }
            if marker.value == "[" {
                let close = matching_bracket(tokens, cursor);
                let Some(close) = close else {
                    dynamic = true;
                    end_offset = marker.end_offset;
                    break;
                };
                let inner = &tokens[cursor + 1..close];
                if inner.len() == 1 && (inner[0].kind == "string" || inner[0].kind == "number") {
                    let index_value = literal_index(&inner[0]);
                    path.push(format!("[{index_value}]"));
                } else {
                    let raw_inner = char_slice(raw, marker.end_offset, tokens[close].start_offset)
                        .trim()
                        .to_string();
                    path.push(format!("[{raw_inner}]"));
                    dynamic = true;
                }
                end_offset = tokens[close].end_offset;
                cursor = close + 1;
                continue;
            }
            break;
        }
        let implicit_scope = implicit_scope_of(&token.value).to_string();
        let resolution_status = if !implicit_scope.is_empty() && !path.is_empty() && !dynamic {
            "resolved"
        } else if dynamic {
            "dynamic"
        } else {
            "unresolved"
        };
        references.push(ElReference {
            root: token.value.clone(),
            path,
            raw: char_slice(raw, token.start_offset, end_offset),
            start_offset: token.start_offset,
            end_offset,
            resolution_status: resolution_status.to_string(),
            implicit_scope,
        });
    }
    references
}

fn matching_bracket(tokens: &[ElToken], opening: usize) -> Option<usize> {
    let mut depth = 0i32;
    for (index, token) in tokens.iter().enumerate().skip(opening) {
        if token.value == "[" {
            depth += 1;
        } else if token.value == "]" {
            depth -= 1;
            if depth == 0 {
                return Some(index);
            }
        }
    }
    None
}

fn literal_index(token: &ElToken) -> String {
    if token.kind == "string" && token.value.chars().count() >= 2 {
        return token.value[1..token.value.len() - 1].to_string();
    }
    token.value.clone()
}

fn state_reads(references: &[ElReference]) -> Vec<ElStateRead> {
    let mut reads: Vec<ElStateRead> = Vec::new();
    for reference in references {
        if reference.implicit_scope.is_empty() {
            continue;
        }
        let mut name = String::new();
        let mut dynamic = true;
        if let Some(first) = reference.path.first() {
            if first.starts_with('[') && first.ends_with(']') {
                name = first[1..first.len() - 1].to_string();
            } else {
                name = first.clone();
            }
            dynamic = name.is_empty() || name.contains(['$', '{', '}', '[', ']', '(', ')']);
        }
        reads.push(ElStateRead {
            implicit_object: reference.root.clone(),
            scope: reference.implicit_scope.clone(),
            name,
            raw: reference.raw.clone(),
            dynamic,
            start_offset: reference.start_offset,
            end_offset: reference.end_offset,
        });
    }
    reads
}

//! Delphi regex extraction — Phase 2 (Tier 3) port of `delphi_analyzer.py`.
//!
//! Delphi is **Family B** 9-tuple model with a unique twist:
//!
//! * **Extraction is regex-based**, even when tree-sitter is available.
//!   Tree-sitter is used ONLY to find `interface` / `implementation` section
//!   line ranges; the regex extractors run on those (or the whole file in
//!   fallback mode). Without a tree-sitter grammar, the entire file is scanned.
//! * `FunctionDef` has NO `exported` field (matches Python).
//! * `FileDef` has NO `imports` / `exports` (matches Python).
//! * `CallEdge` exposes `caller_file`, `call_arity`, `callee_raw` and
//!   `callee_name` (no `callee_qualified` / `callee_simple`).
//! * `FieldDef` is unique to Delphi (with `type_signature`).
//! * `uses_units` is a top-level field on the parse output (not nested in
//!   `file_def`).
//!
//! The Rust port focuses on the regex extraction engine. Tree-sitter section
//! detection is skipped on the first iteration (the upstream plan marks the
//! tree-sitter grammar as unavailable / risky). The fallback path therefore
//! scans the entire file with the same regex patterns.

use std::collections::{HashMap, HashSet};

use once_cell::sync::Lazy;
use regex::Regex;

use crate::symbols::{CallEdge, FieldDef, FunctionDef, NamespaceDef, ParseMeta, RelationEdge, TypeDef};

// ── Regex patterns ──────────────────────────────────────────────────────

static COMMENT_MASK_RE: Lazy<Regex> = Lazy::new(|| {
    // `'([^']|'')*'|\{[^}]*\}|\(\*.*?\*\)|//.*?$`
    Regex::new(r"'([^']|'')*'|\{[^}]*\}|\(\*.*?\*\)|//.*?$").unwrap()
});

static BEGIN_END_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)\b(begin|end)\b").unwrap());

static UNIT_NAME_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?im)^\s*(unit|program|library)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;").unwrap()
});

static USES_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?is)\buses\b\s*([^;]+);").unwrap());

static TYPE_DECL_END_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?is)=\s*(?:packed\s+)?(class|record|interface)\b|\bend\s*;").unwrap()
});

static TYPE_DECL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?im)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(class|record|interface)\b(?:\s*\(([^)]*)\))?",
    )
    .unwrap()
});

static METHOD_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?im)^\s*(?:(class)\s+)?(procedure|function|constructor|destructor)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\([^)]*\))?\s*(?::\s*([^;\n]+))?\s*;",
    )
    .unwrap()
});

static SIGNATURE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?im)^\s*(?:(class)\s+)?(procedure|function|constructor|destructor)\s+([A-Za-z_][A-Za-z0-9_\.]*)\s*(\([^)]*\))?\s*(?::\s*([^;\n]+))?\s*;",
    )
    .unwrap()
});

static FIELD_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?im)^\s*([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)\s*:\s*([^;=\n]+)\s*;",
    )
    .unwrap()
});

static CALL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\b([A-Za-z_][A-Za-z0-9_\.]*)\s*\(").unwrap()
});

static TYPE_USAGE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r":\s*([^;\)\n=]+)").unwrap());

// ── Delphi parse output (Family B 9-tuple) ──────────────────────────────

#[derive(Debug, Default)]
pub struct DelphiParseOutput {
    pub functions: Vec<FunctionDef>,
    pub calls: Vec<CallEdge>,
    pub types: Vec<TypeDef>,
    pub namespaces: Vec<NamespaceDef>,
    pub fields: Vec<FieldDef>,
    pub relations: Vec<RelationEdge>,
    pub file_def: crate::symbols::FileDef,
    pub uses_units: Vec<String>,
    pub parse_meta: ParseMeta,
}

// ── Helpers ─────────────────────────────────────────────────────────────

const PRIMITIVE_TYPES: &[&str] = &[
    "integer",
    "int64",
    "word",
    "longword",
    "cardinal",
    "byte",
    "shortint",
    "smallint",
    "single",
    "double",
    "extended",
    "real",
    "currency",
    "boolean",
    "string",
    "ansistring",
    "widestring",
    "unicodestring",
    "char",
    "widechar",
    "pchar",
    "pointer",
    "variant",
    "olevariant",
    "tobject",
    "nil",
    "void",
];

const FIELD_PREFIX_SKIP: &[&str] = &[
    "public",
    "private",
    "protected",
    "published",
    "strict private",
    "strict protected",
    "class",
    "property",
    "procedure",
    "function",
    "constructor",
    "destructor",
];

const CALL_KEYWORDS_SKIP: &[&str] = &[
    "if",
    "for",
    "while",
    "case",
    "inherited",
    "with",
    "array",
    "setlength",
    "length",
    "high",
    "low",
    "ord",
    "chr",
];

fn strip_comments_and_strings(text: &str) -> String {
    COMMENT_MASK_RE
        .replace_all(text, |caps: &regex::Captures| " ".repeat(caps[0].len()))
        .into_owned()
}

fn find_matching_end_block(text: &str, begin_idx: usize) -> Option<usize> {
    let masked = strip_comments_and_strings(text);
    let mut depth: i32 = 0;
    let mut started = false;
    for token in BEGIN_END_RE.find_iter(&masked[begin_idx..]) {
        let value = token.as_str().to_ascii_lowercase();
        if value == "begin" {
            depth += 1;
            started = true;
        } else {
            if !started {
                continue;
            }
            depth -= 1;
            if depth == 0 {
                let abs_end = begin_idx + token.end();
                let mut end_pos = abs_end;
                let bytes = text.as_bytes();
                while end_pos < text.len() && (bytes[end_pos] as char).is_whitespace() {
                    end_pos += 1;
                }
                if end_pos < text.len() && bytes[end_pos] == b';' {
                    end_pos += 1;
                }
                return Some(end_pos);
            }
        }
    }
    None
}

fn find_matching_paren(text: &str, open_idx: usize) -> Option<usize> {
    let bytes = text.as_bytes();
    let mut depth: i32 = 0;
    let mut in_string = false;
    let mut i = open_idx;
    while i < bytes.len() {
        let ch = bytes[i] as char;
        if in_string {
            if ch == '\'' {
                if i + 1 < bytes.len() && bytes[i + 1] == b'\'' {
                    i += 2;
                    continue;
                }
                in_string = false;
            }
            i += 1;
            continue;
        }
        if ch == '\'' {
            in_string = true;
            i += 1;
            continue;
        }
        if ch == '(' {
            depth += 1;
        } else if ch == ')' {
            depth -= 1;
            if depth == 0 {
                return Some(i);
            }
        }
        i += 1;
    }
    None
}

fn split_identifier_list(text: &str) -> Vec<String> {
    text.split(',')
        .map(|item| item.trim())
        .filter(|item| !item.is_empty())
        .map(|item| item.to_string())
        .collect()
}

fn count_signature_arity(params_text: &str) -> u32 {
    if params_text.is_empty() {
        return 0;
    }
    let mut inside = params_text.trim();
    if inside.starts_with('(') && inside.ends_with(')') {
        inside = &inside[1..inside.len() - 1];
    }
    let inside = inside.trim();
    if inside.is_empty() {
        return 0;
    }

    let segments: Vec<&str> = inside
        .split(';')
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .collect();
    let mut count: u32 = 0;
    for segment in segments {
        let pre_eq = segment.split('=').next().unwrap_or("").trim();
        if let Some(colon_idx) = pre_eq.find(':') {
            let names = pre_eq[..colon_idx].trim();
            if !names.is_empty() {
                count += split_identifier_list(names).len() as u32;
                continue;
            }
        }
        count += 1;
    }
    count
}

fn extract_call_arity(body_text: &str, open_paren_idx: usize) -> u32 {
    let close_idx = match find_matching_paren(body_text, open_paren_idx) {
        Some(i) => i,
        None => return 0,
    };
    let inside = body_text[open_paren_idx + 1..close_idx].trim();
    if inside.is_empty() {
        return 0;
    }
    let bytes = inside.as_bytes();
    let mut depth: i32 = 0;
    let mut count: u32 = 1;
    let mut in_string = false;
    let mut i = 0;
    while i < bytes.len() {
        let ch = bytes[i] as char;
        if in_string {
            if ch == '\'' {
                if i + 1 < bytes.len() && bytes[i + 1] == b'\'' {
                    i += 2;
                    continue;
                }
                in_string = false;
            }
            i += 1;
            continue;
        }
        if ch == '\'' {
            in_string = true;
            i += 1;
            continue;
        }
        if matches!(ch, '(' | '[' | '<') {
            depth += 1;
        } else if matches!(ch, ')' | ']' | '>') {
            depth = (depth - 1).max(0);
        } else if ch == ',' && depth == 0 {
            count += 1;
        }
        i += 1;
    }
    count
}

fn extract_unit_name(text: &str, rel_path: &str) -> String {
    if let Some(caps) = UNIT_NAME_RE.captures(text) {
        return caps[2].to_string();
    }
    let basename = rel_path.rsplit('/').next().unwrap_or(rel_path);
    let stem = basename.rsplit_once('.').map(|(s, _)| s).unwrap_or(basename);
    stem.to_string()
}

fn extract_uses_units(text: &str) -> Vec<String> {
    let masked = strip_comments_and_strings(text);
    let mut results: Vec<String> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for cap in USES_RE.captures_iter(&masked) {
        let chunk = &cap[1];
        for part in chunk.split(',') {
            let part = part.trim();
            if part.is_empty() {
                continue;
            }
            let name = match split_identifier_list(part).into_iter().next() {
                Some(n) => n,
                None => continue,
            };
            if seen.insert(name.to_ascii_lowercase()) {
                results.push(name);
            }
        }
    }
    results
}

fn find_type_declaration_end(text: &str, decl_end_idx: usize) -> Option<usize> {
    // `;` immediately after decl_end_idx means a forward declaration
    let trailing = text[decl_end_idx..]
        .chars()
        .take_while(|c| c.is_whitespace())
        .count();
    let trailing_start = decl_end_idx + trailing;
    if text[trailing_start..].starts_with(';') {
        return Some(trailing_start + 1);
    }
    let masked = strip_comments_and_strings(text);
    let mut depth: i32 = 1;
    for token in TYPE_DECL_END_RE.find_iter(&masked[decl_end_idx..]) {
        let opens_kind = token.as_str().to_ascii_lowercase().contains("class")
            || token.as_str().to_ascii_lowercase().contains("record")
            || token.as_str().to_ascii_lowercase().contains("interface");
        if opens_kind {
            depth += 1;
            continue;
        }
        depth -= 1;
        if depth == 0 {
            return Some(decl_end_idx + token.end());
        }
    }
    None
}

fn normalize_type_name(text: &str) -> Option<String> {
    let no_generics = {
        let mut out = String::new();
        let mut depth: i32 = 0;
        for ch in text.chars() {
            if ch == '<' {
                depth += 1;
                continue;
            }
            if ch == '>' {
                if depth > 0 {
                    depth -= 1;
                }
                continue;
            }
            out.push(ch);
        }
        out
    };
    let keywords = [
        "const",
        "var",
        "out",
        "array of",
        "class of",
        "packed",
        "reference to",
        "specialize",
        "generic",
    ];
    let mut cleaned = no_generics;
    for kw in keywords {
        let pat = format!(r"(?i)\b{}\b", regex::escape(kw));
        if let Ok(re) = Regex::new(&pat) {
            cleaned = re.replace_all(&cleaned, " ").into_owned();
        }
    }
    cleaned = cleaned.replace('^', " ");
    let ident_re = Regex::new(r"[A-Za-z_][A-Za-z0-9_\.]*").unwrap();
    let m = ident_re.find(&cleaned)?;
    let name = m.as_str().to_string();
    if name.contains('.') {
        Some(name.rsplit('.').next().unwrap_or(&name).to_string())
    } else {
        Some(name)
    }
}

fn split_scope_name(scope: &str) -> Vec<String> {
    scope.split("::").map(|s| s.to_string()).collect()
}

fn register_type_usage(
    source_id: &str,
    source_label: &str,
    type_text: &str,
    rel_path: &str,
    types: &mut Vec<TypeDef>,
    relations: &mut Vec<RelationEdge>,
    type_registry: &mut HashMap<String, TypeDef>,
) {
    let mut parts: Vec<String> = if type_text.contains(':') {
        TYPE_USAGE_RE
            .find_iter(type_text)
            .map(|m| m.as_str().trim_start_matches(':').trim().to_string())
            .collect()
    } else {
        vec![type_text.to_string()]
    };
    if parts.is_empty() {
        parts.push(type_text.to_string());
    }

    let mut seen_local: HashSet<String> = HashSet::new();
    for part in parts {
        let Some(type_name) = normalize_type_name(&part) else {
            continue;
        };
        if PRIMITIVE_TYPES.contains(&type_name.to_ascii_lowercase().as_str()) {
            continue;
        }
        if !seen_local.insert(type_name.clone()) {
            continue;
        }

        let type_id = type_name.clone();
        if !type_registry.contains_key(&type_id) {
            let placeholder = TypeDef {
                symbol_id: type_id.clone(),
                qualified_name: type_name.clone(),
                name: type_name.clone(),
                kind: "external".to_string(),
                file_path: rel_path.to_string(),
                start_line: 0,
                end_line: 0,
                code: type_name.clone(),
                comment: String::new(),
                summary: String::new(),
                note: String::new(),
            };
            type_registry.insert(type_id.clone(), placeholder.clone());
            types.push(placeholder);
        }

        relations.push(RelationEdge {
            source_id: source_id.to_string(),
            source_label: source_label.to_string(),
            target_id: type_id.clone(),
            target_label: "Type".to_string(),
            rel_type: "USES_TYPE".to_string(),
            properties: Default::default(),
        });

        if part.contains('^') {
            let mut props = HashMap::new();
            props.insert("kind".to_string(), "pointer".to_string());
            relations.push(RelationEdge {
                source_id: source_id.to_string(),
                source_label: source_label.to_string(),
                target_id: type_id,
                target_label: "Type".to_string(),
                rel_type: "POINTER_TO".to_string(),
                properties: props,
            });
        }
    }
}

fn normalize_call_name(text: &str) -> String {
    let mut cleaned = text.trim().to_string();
    cleaned = cleaned.replace("self.", "");
    cleaned = cleaned.replace("inherited ", "");
    if cleaned.contains('.') {
        cleaned = cleaned.rsplit('.').next().unwrap_or(&cleaned).to_string();
    }
    cleaned.trim().to_string()
}

fn line_for_index(text: &str, byte_idx: usize) -> u32 {
    text[..byte_idx.min(text.len())].bytes().filter(|b| *b == b'\n').count() as u32 + 1
}

fn file_text_from_source(source: &[u8]) -> String {
    String::from_utf8_lossy(source).into_owned()
}

// ── Public entry point ──────────────────────────────────────────────────

pub fn parse_delphi_source(source: &[u8], rel_path: &str) -> Option<DelphiParseOutput> {
    let text = file_text_from_source(source);
    if text.is_empty() {
        return None;
    }

    let file_lines = text.bytes().filter(|b| *b == b'\n').count() as u32 + 1;

    let namespace_name = extract_unit_name(&text, rel_path);
    let namespace_id = crate::symbols::namespace_id(&namespace_name);
    let namespace_def = NamespaceDef {
        symbol_id: namespace_id.clone(),
        qualified_name: namespace_name.clone(),
        name: namespace_name.clone(),
        file_path: rel_path.to_string(),
        start_line: 1,
        end_line: std::cmp::max(1, file_lines),
        code: format!("unit {};", namespace_name),
        comment: String::new(),
        summary: String::new(),
        note: String::new(),
    };

    let uses_units = extract_uses_units(&text);

    let mut types: Vec<TypeDef> = Vec::new();
    let mut functions: Vec<FunctionDef> = Vec::new();
    let mut fields: Vec<FieldDef> = Vec::new();
    let mut relations: Vec<RelationEdge> = Vec::new();
    let mut calls: Vec<CallEdge> = Vec::new();
    let mut function_registry: HashSet<String> = HashSet::new();
    let mut type_registry: HashMap<String, TypeDef> = HashMap::new();

    // ── Type declarations ───────────────────────────────────────────────
    let type_matches: Vec<regex::Match> = TYPE_DECL_RE.find_iter(&text).collect();
    let mut type_block_ranges: Vec<(u32, u32)> = Vec::new();

    for m in &type_matches {
        let Some(caps) = TYPE_DECL_RE.captures(&text[m.start()..]) else {
            continue;
        };
        let type_name = caps[1].to_string();
        let type_kind = caps[2].to_ascii_lowercase();
        let base_types = caps.get(3).map(|c| c.as_str()).unwrap_or("");
        let abs_start = m.start() + caps.get(0).unwrap().start();
        let end_idx = find_type_declaration_end(&text, m.start() + caps.get(0).unwrap().end())
            .unwrap_or_else(|| (m.start() + 800).min(text.len()));
        let snippet = text[abs_start..end_idx].to_string();
        let start_line = line_for_index(&text, abs_start);
        let end_line = line_for_index(&text, end_idx);

        let qualified = if namespace_name.is_empty() {
            type_name.clone()
        } else {
            format!("{}::{}", namespace_name, type_name)
        };
        let type_id = qualified.clone();
        if type_registry.contains_key(&type_id) {
            continue;
        }
        let type_def = TypeDef {
            symbol_id: type_id.clone(),
            qualified_name: qualified.clone(),
            name: type_name.clone(),
            kind: type_kind,
            file_path: rel_path.to_string(),
            start_line,
            end_line,
            code: snippet.clone(),
            comment: String::new(),
            summary: String::new(),
            note: String::new(),
        };
        type_registry.insert(type_id.clone(), type_def.clone());
        types.push(type_def);
        type_block_ranges.push((start_line, end_line));

        // Base types
        for base_item in base_types.split(',').map(|s| s.trim()).filter(|s| !s.is_empty()) {
            let Some(base_name) = normalize_type_name(base_item) else {
                continue;
            };
            let base_id = base_name.clone();
            if !type_registry.contains_key(&base_id) {
                let placeholder = TypeDef {
                    symbol_id: base_id.clone(),
                    qualified_name: base_name.clone(),
                    name: base_name.clone(),
                    kind: "external".to_string(),
                    file_path: rel_path.to_string(),
                    start_line: 0,
                    end_line: 0,
                    code: base_name.clone(),
                    comment: String::new(),
                    summary: String::new(),
                    note: String::new(),
                };
                type_registry.insert(base_id.clone(), placeholder.clone());
                types.push(placeholder);
            }
            relations.push(RelationEdge {
                source_id: type_id.clone(),
                source_label: "Type".to_string(),
                target_id: base_id,
                target_label: "Type".to_string(),
                rel_type: "EXTENDS".to_string(),
                properties: Default::default(),
            });
        }

        // Methods inside the type body
        let body_start = m.start() + caps.get(0).unwrap().end();
        let body_text = &text[body_start..end_idx];
        for method_cap in METHOD_RE.captures_iter(body_text) {
            let method_name = method_cap.get(3).map(|c| c.as_str()).unwrap_or("").trim();
            if method_name.is_empty() {
                continue;
            }
            let params_text = method_cap.get(4).map(|c| c.as_str()).unwrap_or("").trim();
            let return_type_text = method_cap.get(5).map(|c| c.as_str()).unwrap_or("").trim();
            let method_kind = method_cap
                .get(2)
                .map(|c| c.as_str())
                .unwrap_or("")
                .to_ascii_lowercase();
            let arity = count_signature_arity(params_text);
            let method_scope = qualified.clone();
            let method_symbol_id = crate::symbols::function_symbol_id(
                Some(&method_scope),
                method_name,
                arity,
                rel_path,
            );
            if !function_registry.insert(method_symbol_id.clone()) {
                continue;
            }
            let method_start_idx = body_start + method_cap.get(0).unwrap().start();
            let method_end_idx = body_start + method_cap.get(0).unwrap().end();
            let method_start_line = line_for_index(&text, method_start_idx);
            let method_end_line = line_for_index(&text, method_end_idx);
            let method_snippet = text[method_start_idx..method_end_idx].to_string();

            let kind_final = if method_kind == "constructor" || method_kind == "destructor" {
                format!("{}_declaration", method_kind)
            } else {
                "declaration".to_string()
            };

            functions.push(FunctionDef {
                symbol_id: method_symbol_id.clone(),
                qualified_name: crate::symbols::qualified_name(Some(&method_scope), method_name),
                name: method_name.to_string(),
                kind: kind_final,
                scope_name: Some(method_scope.clone()),
                file_path: rel_path.to_string(),
                start_byte: method_start_idx as u32,
                end_byte: method_end_idx as u32,
                start_line: method_start_line,
                end_line: method_end_line,
                arity,
                code: method_snippet,
                comment: String::new(),
                summary: String::new(),
                note: String::new(),
            });

            relations.push(RelationEdge {
                source_id: type_id.clone(),
                source_label: "Type".to_string(),
                target_id: method_symbol_id.clone(),
                target_label: "Function".to_string(),
                rel_type: "DECLARES".to_string(),
                properties: {
                    let mut p = HashMap::new();
                    p.insert("declared_in_type".to_string(), "true".to_string());
                    p
                },
            });

            if !namespace_name.is_empty() {
                relations.push(RelationEdge {
                    source_id: namespace_id.clone(),
                    source_label: "Namespace".to_string(),
                    target_id: method_symbol_id.clone(),
                    target_label: "Function".to_string(),
                    rel_type: "CONTAINS".to_string(),
                    properties: Default::default(),
                });
            }

            register_type_usage(
                &method_symbol_id,
                "Function",
                params_text,
                rel_path,
                &mut types,
                &mut relations,
                &mut type_registry,
            );
            if !return_type_text.is_empty() {
                register_type_usage(
                    &method_symbol_id,
                    "Function",
                    return_type_text,
                    rel_path,
                    &mut types,
                    &mut relations,
                    &mut type_registry,
                );
            }
        }

        // Field declarations inside the type body
        for field_cap in FIELD_RE.captures_iter(body_text) {
            let prefix = field_cap[1].trim().to_ascii_lowercase();
            if FIELD_PREFIX_SKIP.contains(&prefix.as_str()) {
                continue;
            }
            let type_sig = field_cap[2].trim().to_string();
            let line = line_for_index(&text, body_start + field_cap.get(0).unwrap().start());
            for field_name in split_identifier_list(&field_cap[1]) {
                let field_id = format!("{}::{}@{}", qualified, field_name, rel_path);
                fields.push(FieldDef {
                    symbol_id: field_id.clone(),
                    qualified_name: format!("{}::{}", qualified, field_name),
                    name: field_name,
                    scope_name: Some(qualified.clone()),
                    type_signature: type_sig.clone(),
                    file_path: rel_path.to_string(),
                    start_line: line,
                    end_line: line,
                    code: field_cap[0].to_string(),
                });
                relations.push(RelationEdge {
                    source_id: type_id.clone(),
                    source_label: "Type".to_string(),
                    target_id: field_id.clone(),
                    target_label: "Field".to_string(),
                    rel_type: "DECLARES".to_string(),
                    properties: Default::default(),
                });
                register_type_usage(
                    &field_id,
                    "Field",
                    &type_sig,
                    rel_path,
                    &mut types,
                    &mut relations,
                    &mut type_registry,
                );
            }
        }
    }

    // ── Top-level function signatures ───────────────────────────────────
    let declaration_skip: Vec<(u32, u32)> = type_block_ranges.clone();
    let sig_matches: Vec<regex::Match> = SIGNATURE_RE.find_iter(&text).collect();
    for idx in 0..sig_matches.len() {
        let m = &sig_matches[idx];
        let Some(caps) = SIGNATURE_RE.captures(&text[m.start()..]) else {
            continue;
        };
        let is_class = caps.get(1).is_some();
        let kind = caps
            .get(2)
            .map(|c| c.as_str().to_ascii_lowercase())
            .unwrap_or_default();
        let raw_name = caps.get(3).map(|c| c.as_str().trim().to_string()).unwrap_or_default();
        let params_text = caps.get(4).map(|c| c.as_str().trim().to_string()).unwrap_or_default();
        let return_type_text = caps.get(5).map(|c| c.as_str().trim().to_string()).unwrap_or_default();

        let next_start = if idx + 1 < sig_matches.len() {
            sig_matches[idx + 1].start()
        } else {
            text.len()
        };

        let local_scope = "";
        let mut func_name = raw_name.clone();
        let mut scope_head = local_scope.to_string();
        if raw_name.contains('.') {
            if let Some((scope, name)) = raw_name.rsplit_once('.') {
                scope_head = scope.to_string();
                func_name = name.to_string();
            }
        }

        let mut scope_parts: Vec<String> = Vec::new();
        if !namespace_name.is_empty() {
            scope_parts.push(namespace_name.clone());
        }
        for part in scope_head.split('.').filter(|s| !s.is_empty()) {
            scope_parts.push(part.to_string());
        }
        // Dedup
        let mut seen_parts: HashSet<String> = HashSet::new();
        scope_parts.retain(|p| seen_parts.insert(p.clone()));
        let scope_name: Option<String> = if scope_parts.is_empty() {
            None
        } else {
            Some(scope_parts.join("::"))
        };

        let signature_start = m.start();
        let start_line = line_for_index(&text, signature_start);
        // Skip if inside a type block AND raw_name has no `.` (declarations
        // were already captured by the type-body method pass).
        if !raw_name.contains('.') && declaration_skip.iter().any(|(s, e)| *s <= start_line && start_line <= *e) {
            continue;
        }

        let arity = count_signature_arity(&params_text);

        // Look for a body after the signature. We must SEARCH only AFTER the
        // signature; otherwise the regex can match a `begin` from a previous
        // function's body that lives between the two signatures.
        let body_end = {
            let sig_end = m.start() + caps.get(0).unwrap().end();
            let after_sig = &text[sig_end..next_start];
            let begin_re = Regex::new(r"(?is)\bbegin\b").unwrap();
            // Find the first `begin` token that is preceded by `;` (or is
            // the very first character) — that signals "this function's
            // body", not a stray word inside another routine.
            let mut body_begin: Option<usize> = None;
            for begin_match in begin_re.find_iter(after_sig) {
                let before = &after_sig[..begin_match.start()];
                let trimmed = before.trim_end();
                if trimmed.is_empty() || trimmed.ends_with(';') {
                    body_begin = Some(sig_end + begin_match.start());
                    break;
                }
            }
            body_begin.and_then(|begin_idx| find_matching_end_block(&text, begin_idx))
        };

        let (end_idx, function_kind) = if let Some(be) = body_end {
            (be, "function")
        } else {
            (m.start() + caps.get(0).unwrap().end(), "declaration")
        };

        let end_line = line_for_index(&text, end_idx);
        let snippet = text[signature_start..end_idx].to_string();

        let qualified_str = scope_name
            .as_deref()
            .map(|s| crate::symbols::qualified_name(Some(s), &func_name))
            .unwrap_or_else(|| func_name.clone());
        let symbol_id = crate::symbols::function_symbol_id(
            scope_name.as_deref(),
            &func_name,
            arity,
            rel_path,
        );

        let mut replaced = false;
        for f in functions.iter_mut() {
            if f.symbol_id == symbol_id {
                if body_end.is_none() {
                    replaced = true;
                    break;
                }
                // Update with full body version
                f.qualified_name = qualified_str.clone();
                f.name = func_name.clone();
                f.kind = if kind == "constructor" || kind == "destructor" {
                    format!("{}_{}", kind, function_kind)
                } else {
                    function_kind.to_string()
                };
                f.scope_name = scope_name.clone();
                f.start_line = start_line;
                f.end_line = end_line;
                f.arity = arity;
                f.code = snippet.clone();
                replaced = true;
                break;
            }
        }
        if !replaced {
            if function_kind == "function" {
                function_registry.insert(symbol_id.clone());
            }
            let kind_final = if kind == "constructor" || kind == "destructor" {
                format!("{}_{}", kind, function_kind)
            } else {
                function_kind.to_string()
            };
            functions.push(FunctionDef {
                symbol_id: symbol_id.clone(),
                qualified_name: qualified_str,
                name: func_name.clone(),
                kind: kind_final,
                scope_name: scope_name.clone(),
                file_path: rel_path.to_string(),
                start_byte: signature_start as u32,
                end_byte: end_idx as u32,
                start_line,
                end_line,
                arity,
                code: snippet,
                comment: String::new(),
                summary: String::new(),
                note: String::new(),
            });

            // DECLARES relation if scope is a known type
            if let Some(s) = scope_name.as_ref() {
                let type_id_check = s.clone();
                if type_registry.contains_key(&type_id_check) {
                    let mut props = HashMap::new();
                    if is_class {
                        props.insert("static".to_string(), "true".to_string());
                    }
                    relations.push(RelationEdge {
                        source_id: type_id_check,
                        source_label: "Type".to_string(),
                        target_id: symbol_id.clone(),
                        target_label: "Function".to_string(),
                        rel_type: "DECLARES".to_string(),
                        properties: Default::default(),
                    });
                }
            }
            if !namespace_name.is_empty() {
                let mut props = HashMap::new();
                props.insert("static".to_string(), is_class.to_string());
                relations.push(RelationEdge {
                    source_id: namespace_id.clone(),
                    source_label: "Namespace".to_string(),
                    target_id: symbol_id.clone(),
                    target_label: "Function".to_string(),
                    rel_type: "CONTAINS".to_string(),
                    properties: props,
                });
            }
            register_type_usage(
                &symbol_id,
                "Function",
                &params_text,
                rel_path,
                &mut types,
                &mut relations,
                &mut type_registry,
            );
            if !return_type_text.is_empty() {
                register_type_usage(
                    &symbol_id,
                    "Function",
                    &return_type_text,
                    rel_path,
                    &mut types,
                    &mut relations,
                    &mut type_registry,
                );
            }
        }

        if body_end.is_none() {
            continue;
        }

        // ── Call extraction inside the body ──
        let body_text = &text[m.start() + caps.get(0).unwrap().end()..end_idx];
        for call_cap in CALL_RE.captures_iter(body_text) {
            let raw_call = call_cap.get(1).map(|c| c.as_str()).unwrap_or("").trim();
            if raw_call.is_empty() {
                continue;
            }
            let lowered = raw_call.to_ascii_lowercase();
            if CALL_KEYWORDS_SKIP.contains(&lowered.as_str()) {
                continue;
            }
            let open_idx = call_cap.get(0).unwrap().end() - 1;
            let arity_guess = extract_call_arity(body_text, open_idx);
            let call_line = line_for_index(
                &text,
                m.start() + caps.get(0).unwrap().end() + call_cap.get(0).unwrap().start(),
            );
            calls.push(CallEdge {
                caller_id: symbol_id.clone(),
                caller_file: rel_path.to_string(),
                caller_scope: scope_name.clone(),
                call_line,
                call_column: 0,
                call_start_byte: 0,
                call_branch_kind: "none".to_string(),
                call_loop_depth: 0,
                call_control_frames_json: "[]".to_string(),
                call_type: "call_expression".to_string(),
                call_arity: arity_guess,
                callee_name: normalize_call_name(raw_call),
                callee_id: None,
            });
        }
    }

    // ── Namespace → Type CONTAINS relations ─────────────────────────────
    for type_def in &types {
        if type_def.kind == "external" {
            continue;
        }
        relations.push(RelationEdge {
            source_id: namespace_id.clone(),
            source_label: "Namespace".to_string(),
            target_id: type_def.symbol_id.clone(),
            target_label: "Type".to_string(),
            rel_type: "CONTAINS".to_string(),
            properties: Default::default(),
        });
    }
    for field in &fields {
        relations.push(RelationEdge {
            source_id: namespace_id.clone(),
            source_label: "Namespace".to_string(),
            target_id: field.symbol_id.clone(),
            target_label: "Field".to_string(),
            rel_type: "CONTAINS".to_string(),
            properties: Default::default(),
        });
    }

    // Touch symbol helpers to suppress dead-code warnings on unused items.
    let _ = split_scope_name;

    let file_def = crate::symbols::FileDef {
        file_path: rel_path.to_string(),
        start_line: 1,
        end_line: std::cmp::max(1, file_lines),
        code: text.clone(),
        comment: String::new(),
        summary: String::new(),
        note: String::new(),
    };

    let parse_meta = ParseMeta {
        parser_language: "regex_fallback".to_string(),
        parser_language_initial: "regex_fallback".to_string(),
        header_retry_attempted: false,
        header_retry_selected: false,
        has_error: false,
        error_nodes: 0,
        error_nodes_initial: 0,
        header_retry_error_nodes: None,
        header_retry_has_error: None,
    };

    Some(DelphiParseOutput {
        functions,
        calls,
        types,
        namespaces: vec![namespace_def],
        fields,
        relations,
        file_def,
        uses_units,
        parse_meta,
    })
}

// ── Tests ───────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    const SIMPLE_PASCAL: &[u8] = b"\
unit MyUnit;

interface

uses SysUtils, Classes;

type
  TBase = class
    procedure DoBase; virtual;
  end;

  TChild = class(TBase)
    FName: string;
    FCount: Integer;
    constructor Create;
    procedure DoBase; override;
    function GetName: string;
  end;

procedure HelperProc(x: Integer; y: string);

implementation

procedure TChild.DoBase;
begin
  WriteLn('DoBase');
  HelperProc(42, 'hello');
end;

constructor TChild.Create;
begin
  FName := 'init';
  FCount := 0;
end;

function TChild.GetName: string;
begin
  Result := FName;
end;

procedure HelperProc(x: Integer; y: string);
begin
  WriteLn(x);
  WriteLn(y);
end;

end.
";

    #[test]
    fn parse_extracts_namespace_unit() {
        let out = parse_delphi_source(SIMPLE_PASCAL, "MyUnit.pas").unwrap();
        assert_eq!(out.namespaces.len(), 1);
        assert_eq!(out.namespaces[0].name, "MyUnit");
        assert_eq!(out.uses_units, vec!["SysUtils", "Classes"]);
    }

    #[test]
    fn parse_extracts_types_with_inheritance() {
        let out = parse_delphi_source(SIMPLE_PASCAL, "MyUnit.pas").unwrap();
        let names: Vec<&str> = out.types.iter().map(|t| t.name.as_str()).collect();
        assert!(names.contains(&"TBase"));
        assert!(names.contains(&"TChild"));
        let child = out.types.iter().find(|t| t.name == "TChild").unwrap();
        assert_eq!(child.kind, "class");
    }

    #[test]
    fn parse_extracts_inheritance_relation() {
        let out = parse_delphi_source(SIMPLE_PASCAL, "MyUnit.pas").unwrap();
        let extends: Vec<&RelationEdge> = out
            .relations
            .iter()
            .filter(|r| r.rel_type == "EXTENDS")
            .collect();
        let child_extends = extends
            .iter()
            .find(|r| r.source_id.ends_with("::TChild"))
            .expect("TChild should EXTENDS TBase");
        assert_eq!(child_extends.target_id, "TBase");
    }

    #[test]
    fn parse_extracts_fields() {
        let out = parse_delphi_source(SIMPLE_PASCAL, "MyUnit.pas").unwrap();
        let names: Vec<&str> = out.fields.iter().map(|f| f.name.as_str()).collect();
        assert!(names.contains(&"FName"));
        assert!(names.contains(&"FCount"));
    }

    #[test]
    fn parse_extracts_methods_and_helper() {
        let out = parse_delphi_source(SIMPLE_PASCAL, "MyUnit.pas").unwrap();
        let fn_names: Vec<&str> = out.functions.iter().map(|f| f.name.as_str()).collect();
        assert!(fn_names.contains(&"DoBase"));
        assert!(fn_names.contains(&"Create"));
        assert!(fn_names.contains(&"GetName"));
        assert!(fn_names.contains(&"HelperProc"));
    }

    #[test]
    fn parse_extracts_calls_with_arity() {
        let out = parse_delphi_source(SIMPLE_PASCAL, "MyUnit.pas").unwrap();
        // HelperProc(42, 'hello') should be detected as a call with arity 2
        let helper_calls: Vec<&CallEdge> = out
            .calls
            .iter()
            .filter(|c| c.callee_name == "HelperProc")
            .collect();
        assert!(!helper_calls.is_empty(), "HelperProc call missing");
        let hc = helper_calls[0];
        assert_eq!(hc.call_arity, 2);
    }

    #[test]
    fn parse_uses_units_extracted() {
        let out = parse_delphi_source(SIMPLE_PASCAL, "MyUnit.pas").unwrap();
        assert_eq!(out.uses_units.len(), 2);
        assert!(out.uses_units.contains(&"SysUtils".to_string()));
        assert!(out.uses_units.contains(&"Classes".to_string()));
    }

    #[test]
    fn parse_meta_fallback() {
        let out = parse_delphi_source(SIMPLE_PASCAL, "MyUnit.pas").unwrap();
        assert_eq!(out.parse_meta.parser_language, "regex_fallback");
        assert!(!out.parse_meta.has_error);
    }
}

//! Port phần parse của `code-tiny/tools/delphi/delphi_analyzer.py`: các
//! dataclass payload (FunctionDef/FileDef/NamespaceDef/TypeDef/FieldDef/
//! RelationEdge/CallEdge), toàn bộ helper `_` từng chữ, `parse_delphi_file`,
//! `_scan_delphi_files`.
//!
//! Điểm mấu chốt (parity):
//! * Trích xuất types/functions/fields/calls là REGEX thuần; tree-sitter chỉ
//!   nuôi `parse_meta` (has_error/ERROR nodes) và section ranges. Với grammar
//!   pascal Isopod, node kinds `interface`/`implementation` KHÔNG thoả bộ lọc
//!   "section"/"part" trong `_extract_section_line_ranges_from_tree` nên ranges
//!   luôn rỗng ⇒ extraction KHÔNG lọc dòng (đã verify bằng golden Python).
//! * `_strip_comments_and_strings` Python thay match bằng `" " * (end - start)`
//!   (số KÝ TỰ) để masked giữ nguyên offsets char so với text. Port Rust thay
//!   mỗi ký tự bị mask bằng `len_utf8()` dấu cách để giữ nguyên byte layout ⇒
//!   match positions trên masked map 1:1 về text (byte offsets), hành vi regex
//!   (leftmost-first, \b, `[^;]+`...) giống hệt vì chuỗi ký tự masked tương
//!   đương tại mọi vị trí content.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use regex::Regex;
use tree_sitter::Node;

use cortex_analyzer_framework::scan::matches_extra_ignore;

// ── Data defs (asdict shape khớp Python payload JSON) ───────────────────────

#[derive(Debug, Clone)]
pub struct FunctionDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub scope_name: Option<String>,
    pub file_path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub arity: usize,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
}

#[derive(Debug, Clone)]
pub struct FileDef {
    pub file_path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
}

#[derive(Debug, Clone)]
pub struct NamespaceDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub file_path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
}

#[derive(Debug, Clone)]
pub struct TypeDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub file_path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
}

#[derive(Debug, Clone)]
pub struct FieldDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub scope_name: Option<String>,
    pub type_signature: String,
    pub file_path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub code: String,
}

#[derive(Debug, Clone)]
pub struct RelationEdge {
    pub source_id: String,
    pub source_label: String,
    pub target_id: String,
    pub target_label: String,
    pub rel_type: String,
    pub properties: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone)]
pub struct CallEdge {
    pub caller_id: String,
    pub caller_file: String,
    pub caller_scope: Option<String>,
    pub call_line: usize,
    pub callee_raw: String,
    pub callee_name: String,
    pub call_arity: usize,
    pub callee_id: Option<String>,
}

/// Kết quả `parse_delphi_file` — tuple 9 phần tử phía Python.
pub struct ParsedFile {
    pub functions: Vec<FunctionDef>,
    pub calls: Vec<CallEdge>,
    pub types: Vec<TypeDef>,
    pub namespaces: Vec<NamespaceDef>,
    pub fields: Vec<FieldDef>,
    pub relations: Vec<RelationEdge>,
    pub file_def: FileDef,
    pub uses_units: Vec<String>,
    pub parse_meta: ParseMeta,
}

// Python parse_meta persist qua parse-cache payload (plane Python); các field
// ranges ở đây giữ đủ shape dataclass nhưng không có consumer trên graph-plane
// Rust — dead_code chấp nhận (khớp Python: cũng không dùng cho rows).
#[allow(dead_code)]
#[derive(Debug, Clone)]
pub struct ParseMeta {
    pub parser_language: String,
    pub parser_available: bool,
    pub has_error: bool,
    pub error_nodes: usize,
    pub range_guided_by_tree: bool,
    pub interface_ranges: Vec<(usize, usize)>,
    pub implementation_ranges: Vec<(usize, usize)>,
}

// ── Helpers (`_` functions của Python) ──────────────────────────────────────

/// `_line_from_byte` — Python `source_bytes[:byte_index].count(b"\n") + 1`.
/// (chỉ dùng nội bộ để khớp vị trí; byte \n đơn nên char/byte tương đương).
fn line_of(text: &str, byte_index: usize) -> usize {
    text.as_bytes()[..byte_index].iter().filter(|&&b| b == b'\n').count() + 1
}

/// `_tree_error_stats` — (has_error, ERROR node count).
fn tree_error_stats(root: Node) -> (bool, usize) {
    let has_error = root.has_error();
    let mut error_nodes = 0usize;
    let mut stack = vec![root];
    while let Some(node) = stack.pop() {
        if node.kind() == "ERROR" {
            error_nodes += 1;
        }
        for i in 0..node.child_count() {
            if let Some(child) = node.child(i) {
                stack.push(child);
            }
        }
    }
    (has_error, error_nodes)
}

/// `_extract_file_comment_from_text` — dòng comment đầu file (line-based).
/// Python `splitlines()` tách trên \n/\r\n/\r (và vài separator hiếm) — port
/// cover \n/\r\n/\r (thực tế của source Delphi).
fn split_lines_py(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0usize;
    let bytes = text.as_bytes();
    let mut i = 0usize;
    while i < bytes.len() {
        match bytes[i] {
            b'\n' => {
                lines.push(&text[start..i]);
                i += 1;
                start = i;
            }
            b'\r' => {
                lines.push(&text[start..i]);
                i += 1;
                if i < bytes.len() && bytes[i] == b'\n' {
                    i += 1;
                }
                start = i;
            }
            _ => i += 1,
        }
    }
    lines.push(&text[start..]);
    lines
}

pub fn extract_file_comment_from_text(text: &str) -> String {
    let mut comments: Vec<String> = Vec::new();
    let mut in_block = false;
    for line in split_lines_py(text) {
        let stripped = line.trim();
        if stripped.is_empty() {
            if !comments.is_empty() {
                break;
            }
            continue;
        }
        if in_block {
            comments.push(stripped.to_string());
            if stripped.contains('}') || stripped.contains("*)") {
                in_block = false;
            }
            continue;
        }
        if stripped.starts_with("//") {
            comments.push(stripped.to_string());
            continue;
        }
        if stripped.starts_with('{') || stripped.starts_with("(*") {
            comments.push(stripped.to_string());
            if !(stripped.contains('}') || stripped.contains("*)")) {
                in_block = true;
            }
            continue;
        }
        break;
    }
    comments.join("\n")
}

/// `_build_note` — Summary/Comment/Code sections nối "\n\n".
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

/// `_normalize_call_name` — replace ALL "self."/"inherited ", lấy segment
/// sau dot cuối, strip.
pub fn normalize_call_name(text: &str) -> String {
    let cleaned = text.trim().replace("self.", "").replace("inherited ", "");
    let cleaned = if cleaned.contains('.') {
        match cleaned.rfind('.') {
            Some(idx) => &cleaned[idx + 1..],
            None => cleaned.as_str(),
        }
    } else {
        cleaned.as_str()
    };
    cleaned.trim().to_string()
}

/// `_extract_scope_stack` — "::".join(stack) hoặc None.
pub fn extract_scope_stack(stack: &[String]) -> Option<String> {
    if stack.is_empty() {
        None
    } else {
        Some(stack.join("::"))
    }
}

/// `_symbol_id` — `{scope}::{name}/{arity}@{rel}` hoặc `{name}/{arity}@{rel}`.
pub fn symbol_id(scope: Option<&str>, name: &str, arity: usize, rel_path: &str) -> String {
    match scope {
        Some(scope) if !scope.is_empty() => format!("{scope}::{name}/{arity}@{rel_path}"),
        _ => format!("{name}/{arity}@{rel_path}"),
    }
}

/// `_qualified_name`.
pub fn qualified_name(scope: Option<&str>, name: &str) -> String {
    match scope {
        Some(scope) if !scope.is_empty() => format!("{scope}::{name}"),
        _ => name.to_string(),
    }
}

/// `_namespace_id`.
pub fn namespace_id(name: &str) -> String {
    format!("namespace::{name}")
}

/// `_type_id` — identity trùng qualified.
pub fn type_id(qualified: &str) -> String {
    qualified.to_string()
}

/// `_normalize_type_name` — bỏ generics/modifier/`^`, lấy identifier đầu.
pub fn normalize_type_name(text: &str) -> Option<String> {
    static RE_GENERIC: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    static RE_MODIFIER: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    static RE_IDENT: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re_generic = RE_GENERIC.get_or_init(|| Regex::new(r"<[^<>]*>").unwrap());
    let re_modifier = RE_MODIFIER
        .get_or_init(|| Regex::new(r"(?i)\b(const|var|out|array\s+of|class\s+of|packed|reference\s+to|specialize|generic)\b").unwrap());
    let re_ident = RE_IDENT.get_or_init(|| Regex::new(r"[A-Za-z_][A-Za-z0-9_.]*").unwrap());

    let cleaned = re_generic.replace_all(text, "");
    let cleaned = re_modifier.replace_all(&cleaned, " ");
    let cleaned = cleaned.replace('^', " ");
    let matched = re_ident.find(&cleaned)?;
    let mut name = matched.as_str().to_string();
    if name.contains('.') {
        name = name.rsplit('.').next().unwrap_or(&name).to_string();
    }
    Some(name)
}

/// `_strip_comments_and_strings` — masked string giữ nguyên BYTE layout với
/// text (mỗi ký tự bị mask → `len_utf8()` dấu cách) để mọi byte offset tìm
/// được trên masked map 1:1 về text (khớp kỹ thuật char-offset của Python).
pub fn strip_comments_and_strings(text: &str) -> String {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| {
        Regex::new(r"(?sm)'([^']|'')*'|\{[^}]*\}|\(\*.*?\*\)|//.*?$").unwrap()
    });
    let bytes = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let mut last = 0usize;
    for m in re.find_iter(text) {
        out.push_str(&text[last..m.start()]);
        // Python: " " * (match.end() - match.start()) — MỌI ký tự (kể cả \n
        // trong block comment) thành space; Rust giữ byte-width để offset
        // không trôi (mỗi byte → 1 space).
        for _ in &bytes[m.start()..m.end()] {
            out.push(' ');
        }
        last = m.end();
    }
    out.push_str(&text[last..]);
    out
}

/// `_find_matching_end_block` — depth đếm begin/end trên masked từ begin_idx,
/// trả end position trong TEXT (byte).
pub fn find_matching_end_block(text: &str, masked: &str, begin_idx: usize) -> Option<usize> {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let token_re = RE.get_or_init(|| Regex::new(r"(?i)\b(begin|end)\b").unwrap());
    let mut depth: i64 = 0;
    let mut started = false;
    for token in token_re.find_iter(&masked[begin_idx..]) {
        let value = token.as_str().to_lowercase();
        if value == "begin" {
            depth += 1;
            started = true;
        } else {
            if !started {
                continue;
            }
            depth -= 1;
            if depth == 0 {
                let mut end_pos = begin_idx + token.end();
                let bytes = text.as_bytes();
                while end_pos < bytes.len() && (bytes[end_pos] as char).is_ascii_whitespace() {
                    end_pos += 1;
                }
                if end_pos < bytes.len() && bytes[end_pos] == b';' {
                    end_pos += 1;
                }
                return Some(end_pos);
            }
        }
    }
    None
}

/// `_find_matching_paren` — scan byte (ascii compare ~ char compare Python).
pub fn find_matching_paren(text: &str, open_idx: usize) -> Option<usize> {
    let mut depth: i64 = 0;
    let mut in_string = false;
    let bytes = text.as_bytes();
    let mut i = open_idx;
    while i < bytes.len() {
        let ch = bytes[i];
        if in_string {
            if ch == b'\'' {
                if i + 1 < bytes.len() && bytes[i + 1] == b'\'' {
                    i += 2;
                    continue;
                }
                in_string = false;
            }
            i += 1;
            continue;
        }
        match ch {
            b'\'' => in_string = true,
            b'(' => depth += 1,
            b')' => {
                depth -= 1;
                if depth == 0 {
                    return Some(i);
                }
            }
            _ => {}
        }
        i += 1;
    }
    None
}

/// `_split_identifier_list` — split ',' + strip + bỏ rỗng.
pub fn split_identifier_list(text: &str) -> Vec<String> {
    text.split(',')
        .map(|item| item.trim().to_string())
        .filter(|item| !item.is_empty())
        .collect()
}

/// `_count_signature_arity`.
pub fn count_signature_arity(params_text: &str) -> usize {
    if params_text.is_empty() {
        return 0;
    }
    let mut inside = params_text.trim();
    if inside.starts_with('(') && inside.ends_with(')') && inside.len() >= 2 {
        inside = &inside[1..inside.len() - 1];
    }
    let inside = inside.trim();
    if inside.is_empty() {
        return 0;
    }

    let mut count = 0usize;
    for segment in inside.split(';') {
        let segment = segment.trim();
        if segment.is_empty() {
            continue;
        }
        let part = segment.split('=').next().unwrap_or("").trim();
        if let Some((names, _)) = part.split_once(':') {
            let names = names.trim();
            if !names.is_empty() {
                count += split_identifier_list(names).len();
                continue;
            }
        }
        count += 1;
    }
    count
}

/// `_extract_call_arity` — đếm ',' top-level trong cặp ngoặc (string-aware).
pub fn extract_call_arity(body_text: &str, open_paren_idx: usize) -> usize {
    let close_idx = match find_matching_paren(body_text, open_paren_idx) {
        Some(idx) => idx,
        None => return 0,
    };
    let inside = &body_text[open_paren_idx + 1..close_idx];
    let inside = inside.trim();
    if inside.is_empty() {
        return 0;
    }
    let mut depth: i64 = 0;
    let mut count = 1usize;
    let mut in_string = false;
    let bytes = inside.as_bytes();
    let mut i = 0usize;
    while i < bytes.len() {
        let ch = bytes[i];
        if in_string {
            if ch == b'\'' {
                if i + 1 < bytes.len() && bytes[i + 1] == b'\'' {
                    i += 2;
                    continue;
                }
                in_string = false;
            }
            i += 1;
            continue;
        }
        match ch {
            b'\'' => in_string = true,
            b'(' | b'[' | b'<' => depth += 1,
            b')' | b']' | b'>' => depth = (depth - 1).max(0),
            b',' if depth == 0 => count += 1,
            _ => {}
        }
        i += 1;
    }
    count
}

/// `_extract_unit_name` — unit/program/library header hoặc basename stem.
pub fn extract_unit_name(text: &str, rel_path: &str) -> String {
    static PATTERNS: std::sync::OnceLock<Vec<Regex>> = std::sync::OnceLock::new();
    let patterns = PATTERNS.get_or_init(|| {
        [
            r"(?im)^\s*unit\s+([A-Za-z_][A-Za-z0-9_]*)\s*;",
            r"(?im)^\s*program\s+([A-Za-z_][A-Za-z0-9_]*)\s*;",
            r"(?im)^\s*library\s+([A-Za-z_][A-Za-z0-9_]*)\s*;",
        ]
        .iter()
        .map(|p| Regex::new(p).unwrap())
        .collect()
    });
    for pattern in patterns.iter() {
        if let Some(name) = pattern.captures(text).and_then(|m| m.get(1)) {
            return name.as_str().to_string();
        }
    }
    // os.path.splitext(os.path.basename(rel_path))[0]
    let base = rel_path.rsplit(['/']).next().unwrap_or(rel_path);
    match base.rfind('.') {
        Some(idx) if idx > 0 => base[..idx].to_string(),
        _ => base.to_string(),
    }
}

/// `_line_in_ranges`.
pub fn line_in_ranges(line_no: usize, ranges: &[(usize, usize)]) -> bool {
    if ranges.is_empty() {
        return true;
    }
    ranges.iter().any(|(start, end)| *start <= line_no && line_no <= *end)
}

/// `_extract_uses_units` — uses clauses trên masked, unit names từ TEXT.
pub fn extract_uses_units(text: &str, masked: &str, allowed_line_ranges: Option<&[(usize, usize)]>) -> Vec<String> {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    static UNIT_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(r"(?is)\buses\b\s*([^;]+);").unwrap());
    let unit_re = UNIT_RE.get_or_init(|| Regex::new(r"^([A-Za-z_][A-Za-z0-9_.]*)").unwrap());

    let mut results: Vec<String> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    for m in re.captures_iter(masked) {
        let whole = m.get(0).unwrap();
        let uses_line = line_of(text, whole.start());
        let ranges = allowed_line_ranges.unwrap_or(&[]);
        if !line_in_ranges(uses_line, ranges) {
            continue;
        }
        let group = m.get(1).unwrap();
        let chunk = &text[group.start()..group.end()];
        for part in chunk.split(',') {
            let part = part.trim();
            if part.is_empty() {
                continue;
            }
            let unit_name = match unit_re.captures(part) {
                Some(c) => c.get(1).unwrap().as_str().to_string(),
                None => continue,
            };
            let lowered = unit_name.to_lowercase();
            if seen.contains(&lowered) {
                continue;
            }
            seen.insert(lowered);
            results.push(unit_name);
        }
    }
    results
}

/// `_merge_line_ranges`.
pub fn merge_line_ranges(ranges: Vec<(usize, usize)>) -> Vec<(usize, usize)> {
    if ranges.is_empty() {
        return Vec::new();
    }
    let mut cleaned: Vec<(usize, usize)> = ranges
        .into_iter()
        .filter(|(start, end)| *start != 0 && *end != 0)
        .map(|(start, end)| (start.max(1), end.max(1)))
        .collect();
    cleaned.sort_unstable();
    let mut merged: Vec<(usize, usize)> = Vec::new();
    for (start, end) in cleaned {
        let (start, end) = if start > end { (end, start) } else { (start, end) };
        match merged.last_mut() {
            Some((prev_start, prev_end)) if start <= *prev_end + 1 => {
                *prev_end = (*prev_end).max(end);
                let _ = prev_start;
            }
            _ => merged.push((start, end)),
        }
    }
    merged
}

/// `_find_type_declaration_end` — trả byte index trong TEXT.
pub fn find_type_declaration_end(text: &str, masked: &str, decl_end_idx: usize) -> Option<usize> {
    static TRAILING: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    static TOKEN: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let trailing_re = TRAILING.get_or_init(|| Regex::new(r"(?s)^\s*;").unwrap());
    let token_re = TOKEN.get_or_init(|| {
        Regex::new(r"(?is)=\s*(?:packed\s+)?(class|record|interface)\b|\bend\s*;").unwrap()
    });

    if let Some(trailing) = trailing_re.find(&text[decl_end_idx..]) {
        return Some(decl_end_idx + trailing.end());
    }

    let mut depth: i64 = 1;
    for token in token_re.captures_iter(&masked[decl_end_idx..]) {
        // Python finditer(masked, decl_end_idx): match bắt đầu >= pos;
        // masked/text cùng byte layout nên offset map 1:1 về text.
        if token.get(1).is_some() {
            depth += 1;
            continue;
        }
        depth -= 1;
        if depth == 0 {
            let whole = token.get(0).unwrap();
            return Some(decl_end_idx + whole.end());
        }
    }
    None
}

/// `_register_type_usage` — external type placeholders + USES_TYPE/POINTER_TO.
#[allow(clippy::too_many_arguments)]
fn register_type_usage(
    source_id: &str,
    source_label: &str,
    type_text: &str,
    rel_path: &str,
    types: &mut Vec<TypeDef>,
    relations: &mut Vec<RelationEdge>,
    type_registry: &mut HashSet<String>,
) {
    const PRIMITIVE_TYPES: [&str; 25] = [
        "integer", "int64", "word", "longword", "cardinal", "byte", "shortint", "smallint",
        "single", "double", "extended", "real", "currency", "boolean", "string", "ansistring",
        "widestring", "unicodestring", "char", "widechar", "pchar", "pointer", "variant",
        "olevariant", "tobject",
    ];
    // Python set còn "nil" và "void" — 27 phần tử.
    const PRIMITIVE_EXTRA: [&str; 2] = ["nil", "void"];

    static COLON_PARTS: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re_colon = COLON_PARTS.get_or_init(|| Regex::new(r":\s*([^;\)\n=]+)").unwrap());

    let mut parts: Vec<String> = Vec::new();
    if type_text.contains(':') {
        for m in re_colon.captures_iter(type_text) {
            parts.push(m.get(1).unwrap().as_str().trim().to_string());
        }
    }
    if parts.is_empty() {
        parts = vec![type_text.to_string()];
    }

    let mut seen_local: std::collections::HashSet<String> = std::collections::HashSet::new();
    for part in &parts {
        let type_name = match normalize_type_name(part) {
            Some(name) => name,
            None => continue,
        };
        let lowered = type_name.to_lowercase();
        if PRIMITIVE_TYPES.contains(&lowered.as_str()) || PRIMITIVE_EXTRA.contains(&lowered.as_str())
        {
            continue;
        }
        if !seen_local.insert(type_name.clone()) {
            continue;
        }

        let type_id_str = type_id(&type_name);
        if !type_registry.contains(&type_id_str) {
            types.push(TypeDef {
                symbol_id: type_id_str.clone(),
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
            });
            type_registry.insert(type_id_str.clone());
        }

        relations.push(RelationEdge {
            source_id: source_id.to_string(),
            source_label: source_label.to_string(),
            target_id: type_id_str.clone(),
            target_label: "Type".to_string(),
            rel_type: "USES_TYPE".to_string(),
            properties: serde_json::Map::new(),
        });

        if part.contains('^') {
            let mut props = serde_json::Map::new();
            props.insert("kind".to_string(), serde_json::json!("pointer"));
            relations.push(RelationEdge {
                source_id: source_id.to_string(),
                source_label: source_label.to_string(),
                target_id: type_id_str,
                target_label: "Type".to_string(),
                rel_type: "POINTER_TO".to_string(),
                properties: props,
            });
        }
    }
}

/// `_extract_type_declarations` — regex pass 1: class/record/interface + method
/// declarations trong body + class fields + EXTENDS/DECLARES/CONTAINS edges.
#[allow(clippy::too_many_arguments)]
fn extract_type_declarations(
    text: &str,
    namespace_name: &str,
    rel_path: &str,
    types: &mut Vec<TypeDef>,
    functions: &mut Vec<FunctionDef>,
    fields: &mut Vec<FieldDef>,
    relations: &mut Vec<RelationEdge>,
    type_registry: &mut HashSet<String>,
    function_registry: &mut HashSet<String>,
    type_block_ranges: &mut Vec<(usize, usize)>,
    allowed_line_ranges: Option<&[(usize, usize)]>,
) {
    static TYPE_DECL: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    static METHOD_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    static FIELD_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let type_decl_re = TYPE_DECL.get_or_init(|| {
        Regex::new(
            r"(?im)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(class|record|interface)\b(?:\s*\(([^)]*)\))?",
        )
        .unwrap()
    });
    let method_re = METHOD_RE.get_or_init(|| {
        Regex::new(
            r"(?im)^\s*(?:(class)\s+)?(procedure|function|constructor|destructor)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\([^;\n]*\))?\s*(?::\s*([^;\n]+))?\s*;",
        )
        .unwrap()
    });
    let field_re = FIELD_RE.get_or_init(|| {
        Regex::new(
            r"(?im)^\s*([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)\s*:\s*([^;=\n]+)\s*;",
        )
        .unwrap()
    });

    // Python finditer — quét tuần tự, không mutation trong loop.
    let matches: Vec<regex::Captures> = type_decl_re.captures_iter(text).collect();
    for m in matches {
        let type_name = m.get(1).unwrap().as_str().to_string();
        let type_kind = m.get(2).unwrap().as_str().to_lowercase();
        let base_types = m.get(3).map(|g| g.as_str()).unwrap_or("");
        let whole = m.get(0).unwrap();
        let start_idx = whole.start();
        let decl_end_idx = whole.end();
        let masked_for_end = strip_comments_and_strings(text);
        let end_idx = find_type_declaration_end(text, &masked_for_end, decl_end_idx)
            .unwrap_or_else(|| text.len().min(start_idx + 800));

        let snippet = &text[start_idx..end_idx];
        let start_line = line_of(text, start_idx);
        let end_line = line_of(text, end_idx);
        let ranges = allowed_line_ranges.unwrap_or(&[]);
        if !line_in_ranges(start_line, ranges) {
            continue;
        }

        let qualified = if namespace_name.is_empty() {
            type_name.clone()
        } else {
            format!("{namespace_name}::{type_name}")
        };
        let type_id_str = type_id(&qualified);
        if type_registry.contains(&type_id_str) {
            continue;
        }

        types.push(TypeDef {
            symbol_id: type_id_str.clone(),
            qualified_name: qualified.clone(),
            name: type_name.clone(),
            kind: type_kind,
            file_path: rel_path.to_string(),
            start_line,
            end_line,
            code: snippet.to_string(),
            comment: String::new(),
            summary: String::new(),
            note: String::new(),
        });
        type_registry.insert(type_id_str.clone());
        type_block_ranges.push((start_line, end_line));

        // EXTENDS theo base list.
        for base_item in base_types.split(',').map(str::trim).filter(|s| !s.is_empty()) {
            let base_name = match normalize_type_name(base_item) {
                Some(name) => name,
                None => continue,
            };
            let base_id = type_id(&base_name);
            if !type_registry.contains(&base_id) {
                types.push(TypeDef {
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
                });
                type_registry.insert(base_id.clone());
            }
            relations.push(RelationEdge {
                source_id: type_id_str.clone(),
                source_label: "Type".to_string(),
                target_id: base_id,
                target_label: "Type".to_string(),
                rel_type: "EXTENDS".to_string(),
                properties: serde_json::Map::new(),
            });
        }

        // Method declarations trong type body.
        let body_offset = decl_end_idx;
        let body_text = &text[body_offset..end_idx];
        for method_match in method_re.captures_iter(body_text) {
            let method_name = method_match.get(3).map(|g| g.as_str().trim().to_string()).unwrap_or_default();
            if method_name.is_empty() {
                continue;
            }
            let params_text = method_match.get(4).map(|g| g.as_str().trim().to_string()).unwrap_or_default();
            let return_type_text = method_match.get(5).map(|g| g.as_str().trim().to_string()).unwrap_or_default();
            let method_kind = method_match.get(2).map(|g| g.as_str().to_lowercase()).unwrap_or_default();
            let arity = count_signature_arity(&params_text);
            let method_scope = qualified.clone();
            let method_symbol_id = symbol_id(
                Some(&method_scope),
                &method_name,
                arity,
                rel_path,
            );
            if function_registry.contains(&method_symbol_id) {
                continue;
            }
            function_registry.insert(method_symbol_id.clone());

            let method_start_idx = body_offset + method_match.get(0).unwrap().start();
            let method_end_idx = body_offset + method_match.get(0).unwrap().end();
            let method_start_line = line_of(text, method_start_idx);
            let method_end_line = line_of(text, method_end_idx);
            let method_snippet = &text[method_start_idx..method_end_idx];

            let kind = if method_kind == "constructor" || method_kind == "destructor" {
                format!("{method_kind}_declaration")
            } else {
                "declaration".to_string()
            };
            functions.push(FunctionDef {
                symbol_id: method_symbol_id.clone(),
                qualified_name: qualified_name(Some(&method_scope), &method_name),
                name: method_name.clone(),
                kind,
                scope_name: Some(method_scope.clone()),
                file_path: rel_path.to_string(),
                start_line: method_start_line,
                end_line: method_end_line,
                arity,
                code: method_snippet.to_string(),
                comment: String::new(),
                summary: String::new(),
                note: String::new(),
            });

            let mut declared_props = serde_json::Map::new();
            declared_props.insert("declared_in_type".to_string(), serde_json::json!(true));
            relations.push(RelationEdge {
                source_id: type_id_str.clone(),
                source_label: "Type".to_string(),
                target_id: method_symbol_id.clone(),
                target_label: "Function".to_string(),
                rel_type: "DECLARES".to_string(),
                properties: declared_props,
            });
            if !namespace_name.is_empty() {
                relations.push(RelationEdge {
                    source_id: namespace_id(namespace_name),
                    source_label: "Namespace".to_string(),
                    target_id: method_symbol_id.clone(),
                    target_label: "Function".to_string(),
                    rel_type: "CONTAINS".to_string(),
                    properties: serde_json::Map::new(),
                });
            }

            register_type_usage(
                &method_symbol_id,
                "Function",
                &params_text,
                rel_path,
                types,
                relations,
                type_registry,
            );
            if !return_type_text.is_empty() {
                register_type_usage(
                    &method_symbol_id,
                    "Function",
                    &return_type_text,
                    rel_path,
                    types,
                    relations,
                    type_registry,
                );
            }
        }

        // Class/record fields (declarative).
        const SKIP_PREFIXES: [&str; 12] = [
            "public", "private", "protected", "published", "strict private", "strict protected",
            "class", "property", "procedure", "function", "constructor", "destructor",
        ];
        for field_match in field_re.captures_iter(body_text) {
            let prefix = field_match.get(1).unwrap().as_str().trim().to_lowercase();
            if SKIP_PREFIXES.contains(&prefix.as_str()) {
                continue;
            }
            let type_sig = field_match.get(2).unwrap().as_str().trim().to_string();
            let line = line_of(text, decl_end_idx + field_match.get(0).unwrap().start());
            for field_name in split_identifier_list(field_match.get(1).unwrap().as_str()) {
                let field_id = format!("{qualified}::{field_name}@{rel_path}");
                fields.push(FieldDef {
                    symbol_id: field_id.clone(),
                    qualified_name: format!("{qualified}::{field_name}"),
                    name: field_name,
                    scope_name: Some(qualified.clone()),
                    type_signature: type_sig.clone(),
                    file_path: rel_path.to_string(),
                    start_line: line,
                    end_line: line,
                    code: field_match.get(0).unwrap().as_str().to_string(),
                });
                relations.push(RelationEdge {
                    source_id: type_id_str.clone(),
                    source_label: "Type".to_string(),
                    target_id: field_id.clone(),
                    target_label: "Field".to_string(),
                    rel_type: "DECLARES".to_string(),
                    properties: serde_json::Map::new(),
                });
                register_type_usage(
                    &field_id,
                    "Field",
                    &type_sig,
                    rel_path,
                    types,
                    relations,
                    type_registry,
                );
            }
        }
    }
}

/// `_parse_function_signatures` — regex pass 2: signatures top-level + method
/// implementations (`Type.Method`), body span qua begin/end matching, calls.
#[allow(clippy::too_many_arguments)]
fn parse_function_signatures(
    text: &str,
    namespace_name: &str,
    rel_path: &str,
    types: &mut Vec<TypeDef>,
    functions: &mut Vec<FunctionDef>,
    relations: &mut Vec<RelationEdge>,
    calls: &mut Vec<CallEdge>,
    type_registry: &mut HashSet<String>,
    function_registry: &mut HashSet<String>,
    declaration_skip_line_ranges: Option<&[(usize, usize)]>,
    allowed_line_ranges: Option<&[(usize, usize)]>,
) {
    static SIGNATURE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    static BEGIN_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    static CALL_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let signature_re = SIGNATURE.get_or_init(|| {
        Regex::new(
            r"(?im)^\s*(?:(class)\s+)?(procedure|function|constructor|destructor)\s+([A-Za-z_][A-Za-z0-9_.]*)\s*(\([^;\n]*\))?\s*(?::\s*([^;\n]+))?\s*;",
        )
        .unwrap()
    });
    let begin_re = BEGIN_RE.get_or_init(|| Regex::new(r"(?is)\bbegin\b").unwrap());
    let call_re = CALL_RE.get_or_init(|| Regex::new(r"(?i)\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(").unwrap());

    // Python `_find_matching_end_block` recompute masked per call — deterministic
    // nên cache 1 lần cho cả pass (hành vi giống hệt).
    let masked = strip_comments_and_strings(text);

    const CALL_SKIP: [&str; 13] = [
        "if", "for", "while", "case", "inherited", "with", "array", "setlength", "length",
        "high", "low", "ord", "chr",
    ];

    let matches: Vec<regex::Captures> = signature_re.captures_iter(text).collect();
    for (idx, m) in matches.iter().enumerate() {
        let is_class = m.get(1).is_some();
        let kind = m.get(2).map(|g| g.as_str().to_lowercase()).unwrap_or_default();
        let raw_name = m.get(3).map(|g| g.as_str().trim().to_string()).unwrap_or_default();
        let params_text = m.get(4).map(|g| g.as_str().trim().to_string()).unwrap_or_default();
        let return_type_text = m.get(5).map(|g| g.as_str().trim().to_string()).unwrap_or_default();
        let next_start = if idx + 1 < matches.len() {
            matches[idx + 1].get(0).unwrap().start()
        } else {
            text.len()
        };

        let (local_scope, func_name) = match raw_name.rfind('.') {
            Some(pos) => (raw_name[..pos].to_string(), raw_name[pos + 1..].to_string()),
            None => (String::new(), raw_name.clone()),
        };

        let mut scope_parts: Vec<String> = Vec::new();
        if !namespace_name.is_empty() {
            scope_parts.push(namespace_name.to_string());
        }
        if !local_scope.is_empty() {
            scope_parts.extend(local_scope.split('.').filter(|p| !p.is_empty()).map(str::to_string));
        }
        let scope_name = extract_scope_stack(&scope_parts);

        let whole = m.get(0).unwrap();
        let signature_start = whole.start();
        let start_line = line_of(text, signature_start);
        let ranges = allowed_line_ranges.unwrap_or(&[]);
        if !line_in_ranges(start_line, ranges) {
            continue;
        }

        if !raw_name.contains('.')
            && let Some(skip) = declaration_skip_line_ranges
            && !skip.is_empty()
            && line_in_ranges(start_line, skip)
        {
            continue;
        }

        let arity = count_signature_arity(&params_text);
        // begin match trong text[match.end():next_start].
        let begin_match = begin_re.find(&text[whole.end()..next_start]);
        let body_end = begin_match.and_then(|b| {
            let begin_idx = whole.end() + b.start();
            find_matching_end_block(text, &masked, begin_idx)
        });

        let (end_idx, function_kind) = match body_end {
            Some(end) => (end, "function"),
            None => (whole.end(), "declaration"),
        };

        let end_line = line_of(text, end_idx);
        let snippet = &text[signature_start..end_idx];

        let sid = symbol_id(scope_name.as_deref(), &func_name, arity, rel_path);
        let qualified = qualified_name(scope_name.as_deref(), &func_name);
        let kind_final = if kind == "constructor" || kind == "destructor" {
            format!("{kind}_{function_kind}")
        } else {
            function_kind.to_string()
        };
        let function_payload = FunctionDef {
            symbol_id: sid.clone(),
            qualified_name: qualified.clone(),
            name: func_name.clone(),
            kind: kind_final,
            scope_name: scope_name.clone(),
            file_path: rel_path.to_string(),
            start_line,
            end_line,
            arity,
            code: snippet.to_string(),
            comment: String::new(),
            summary: String::new(),
            note: String::new(),
        };

        let existing_idx = functions.iter().position(|item| item.symbol_id == sid);
        match existing_idx {
            Some(pos) => {
                if body_end.is_none() {
                    continue;
                }
                functions[pos] = function_payload;
            }
            None => {
                function_registry.insert(sid.clone());
                functions.push(function_payload);

                if let Some(scope) = &scope_name {
                    let tid = type_id(scope);
                    if type_registry.contains(&tid) {
                        relations.push(RelationEdge {
                            source_id: tid,
                            source_label: "Type".to_string(),
                            target_id: sid.clone(),
                            target_label: "Function".to_string(),
                            rel_type: "DECLARES".to_string(),
                            properties: serde_json::Map::new(),
                        });
                    }
                }
                if !namespace_name.is_empty() {
                    let mut props = serde_json::Map::new();
                    props.insert("static".to_string(), serde_json::json!(is_class));
                    relations.push(RelationEdge {
                        source_id: namespace_id(namespace_name),
                        source_label: "Namespace".to_string(),
                        target_id: sid.clone(),
                        target_label: "Function".to_string(),
                        rel_type: "CONTAINS".to_string(),
                        properties: props,
                    });
                }

                register_type_usage(
                    &sid,
                    "Function",
                    &params_text,
                    rel_path,
                    types,
                    relations,
                    type_registry,
                );
                if !return_type_text.is_empty() {
                    register_type_usage(
                        &sid,
                        "Function",
                        &return_type_text,
                        rel_path,
                        types,
                        relations,
                        type_registry,
                    );
                }
            }
        }

        let body_end = match body_end {
            Some(end) => end,
            None => continue,
        };
        let body_text = &text[whole.end()..body_end];
        for call_match in call_re.captures_iter(body_text) {
            let raw_call = call_match.get(1).map(|g| g.as_str().trim().to_string()).unwrap_or_default();
            if raw_call.is_empty() {
                continue;
            }
            let lowered = raw_call.to_lowercase();
            if CALL_SKIP.contains(&lowered.as_str()) {
                continue;
            }
            // open paren = ký tự cuối của match (1 byte) trong body_text.
            let open_idx_rel = call_match.get(0).unwrap().end() - 1;
            let arity_guess = extract_call_arity(body_text, open_idx_rel);
            let call_line = line_of(text, whole.end() + call_match.get(0).unwrap().start());
            calls.push(CallEdge {
                caller_id: sid.clone(),
                caller_file: rel_path.to_string(),
                caller_scope: scope_name.clone(),
                call_line,
                callee_raw: raw_call.clone(),
                callee_name: normalize_call_name(&raw_call),
                call_arity: arity_guess,
                callee_id: None,
            });
        }
    }
}

/// Cặp (interface, implementation) line ranges.
pub type SectionRanges = (Vec<(usize, usize)>, Vec<(usize, usize)>);

/// `_extract_section_line_ranges_from_tree` — DFS stack (children đẩy reversed
/// để duyệt đúng thứ tự), lọc kinds chứa interface/implementation + section/part.
fn extract_section_line_ranges_from_tree(root: Node) -> SectionRanges {
    let mut interface_ranges: Vec<(usize, usize)> = Vec::new();
    let mut implementation_ranges: Vec<(usize, usize)> = Vec::new();
    let interface_types = ["interface_section", "interface_part", "unit_interface"];
    let implementation_types = ["implementation_section", "implementation_part", "unit_implementation"];

    let mut stack = vec![root];
    while let Some(node) = stack.pop() {
        let node_type = node.kind().to_lowercase();
        let start_line = node.start_position().row + 1;
        let end_line = node.end_position().row + 1;
        let has_section_word =
            |node_type: &str| node_type.contains("section") || node_type.contains("part");
        if interface_types.contains(&node_type.as_str())
            || (node_type.contains("interface") && has_section_word(&node_type))
        {
            interface_ranges.push((start_line, end_line));
        } else if implementation_types.contains(&node_type.as_str())
            || (node_type.contains("implementation") && has_section_word(&node_type))
        {
            implementation_ranges.push((start_line, end_line));
        }
        // Python đẩy children REVERSED vào stack; merge_line_ranges sort nên
        // thứ tự duyệt không ảnh hưởng kết quả.
        let child_count = node.child_count();
        for i in 0..child_count {
            if let Some(child) = node.child(i) {
                stack.push(child);
            }
        }
    }

    (merge_line_ranges(interface_ranges), merge_line_ranges(implementation_ranges))
}

/// `parse_delphi_file` — tuple 9 phần tử (functions, calls, types, namespaces,
/// fields, relations, file_def, uses_units, parse_meta).
pub fn parse_delphi_file(path: &Path, root: &Path) -> std::io::Result<ParsedFile> {
    let source_bytes = std::fs::read(path)?;
    let rel_path = rel_path_of(root, path);
    let text = cortex_analyzer_framework::ts::decode_ignore(&source_bytes);

    // Parser: tree-sitter-pascal (Isopod — cùng dòng grammar với
    // tree_sitter_language_pack `pascal` của Python reference).
    let parser_result: Result<tree_sitter::Tree, ()> = (|| {
        let mut parser = tree_sitter::Parser::new();
        parser.set_language(&tree_sitter_pascal::LANGUAGE.into()).map_err(|_| ())?;
        parser.parse(&source_bytes, None).ok_or(())
    })();
    let (tree, parser_available) = match parser_result {
        Ok(tree) => (Some(tree), true),
        Err(()) => (None, false),
    };

    let (has_error, error_nodes) = tree
        .as_ref()
        .map(|tree| tree_error_stats(tree.root_node()))
        .unwrap_or((false, 0));
    let (interface_ranges, implementation_ranges) = tree
        .as_ref()
        .map(|tree| extract_section_line_ranges_from_tree(tree.root_node()))
        .unwrap_or_default();
    let parser_language = if parser_available {
        "delphi_tree_sitter"
    } else {
        "regex_fallback"
    };
    let parser_guided_ranges = !interface_ranges.is_empty() || !implementation_ranges.is_empty();
    let all_section_ranges: Vec<(usize, usize)> = {
        let mut combined = interface_ranges.clone();
        combined.extend(implementation_ranges.iter().cloned());
        merge_line_ranges(combined)
    };

    let file_comment = extract_file_comment_from_text(&text);
    let file_summary = file_comment.clone();
    let file_note = build_note(&text, &file_comment, &file_summary);
    let text_line_count = text.as_bytes().iter().filter(|&&b| b == b'\n').count() + 1;
    let file_def = FileDef {
        file_path: rel_path.clone(),
        start_line: 1,
        end_line: text_line_count,
        code: text.clone(),
        comment: file_comment,
        summary: file_summary,
        note: file_note,
    };

    let namespace_name = extract_unit_name(&text, &rel_path);
    let ns_id = namespace_id(&namespace_name);
    let namespace_def = NamespaceDef {
        symbol_id: ns_id.clone(),
        qualified_name: namespace_name.clone(),
        name: namespace_name.clone(),
        file_path: rel_path.clone(),
        start_line: 1,
        end_line: text_line_count.max(1),
        code: format!("unit {namespace_name};"),
        comment: String::new(),
        summary: String::new(),
        note: String::new(),
    };

    let uses_units = {
        let masked = strip_comments_and_strings(&text);
        // Python: `all_section_ranges or None` — rỗng ⇒ None ⇒ không lọc.
        let allowed = if all_section_ranges.is_empty() {
            None
        } else {
            Some(&all_section_ranges[..])
        };
        extract_uses_units(&text, &masked, allowed)
    };

    let mut types: Vec<TypeDef> = Vec::new();
    let mut functions: Vec<FunctionDef> = Vec::new();
    let mut fields: Vec<FieldDef> = Vec::new();
    let mut relations: Vec<RelationEdge> = Vec::new();
    let mut calls: Vec<CallEdge> = Vec::new();
    let mut function_registry: HashSet<String> = HashSet::new();
    let mut type_block_ranges: Vec<(usize, usize)> = Vec::new();
    let mut type_registry: HashSet<String> = HashSet::new();

    let type_allowed: Option<&[(usize, usize)]> = if !interface_ranges.is_empty() {
        Some(&interface_ranges)
    } else if !all_section_ranges.is_empty() {
        Some(&all_section_ranges)
    } else {
        None
    };
    extract_type_declarations(
        &text,
        &namespace_name,
        &rel_path,
        &mut types,
        &mut functions,
        &mut fields,
        &mut relations,
        &mut type_registry,
        &mut function_registry,
        &mut type_block_ranges,
        type_allowed,
    );
    let skip_ranges = merge_line_ranges(type_block_ranges.clone());
    parse_function_signatures(
        &text,
        &namespace_name,
        &rel_path,
        &mut types,
        &mut functions,
        &mut relations,
        &mut calls,
        &mut type_registry,
        &mut function_registry,
        if skip_ranges.is_empty() { None } else { Some(&skip_ranges) },
        if all_section_ranges.is_empty() { None } else { Some(&all_section_ranges) },
    );

    // Namespace CONTAINS types (kind != external) + fields.
    for type_def in &types {
        if type_def.kind == "external" {
            continue;
        }
        relations.push(RelationEdge {
            source_id: ns_id.clone(),
            source_label: "Namespace".to_string(),
            target_id: type_def.symbol_id.clone(),
            target_label: "Type".to_string(),
            rel_type: "CONTAINS".to_string(),
            properties: serde_json::Map::new(),
        });
    }
    for field in &fields {
        relations.push(RelationEdge {
            source_id: ns_id.clone(),
            source_label: "Namespace".to_string(),
            target_id: field.symbol_id.clone(),
            target_label: "Field".to_string(),
            rel_type: "CONTAINS".to_string(),
            properties: serde_json::Map::new(),
        });
    }

    let parse_meta = ParseMeta {
        parser_language: parser_language.to_string(),
        parser_available,
        has_error,
        error_nodes,
        range_guided_by_tree: parser_guided_ranges,
        interface_ranges,
        implementation_ranges,
    };

    Ok(ParsedFile {
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

/// `os.path.relpath(path, root)` (unix) — path nằm dưới root.
fn rel_path_of(root: &Path, path: &Path) -> String {
    cortex_analyzer_framework::scan::rel_posix(root, path)
}

/// `_should_ignore_directory` — ignore set Delphi-specific (định nghĩa riêng,
/// KHÔNG dùng COMMON_SCAN_EXCLUDE của python_analyzer).
fn should_ignore_directory(dir_name: &str) -> bool {
    const IGNORE_PATTERNS: [&str; 30] = [
        ".git", ".svn", ".hg", //
        ".idea", ".vscode", //
        "__history__", "__recovery__", "lib", "dcu", "dcp", //
        "bin", "obj", "build", "out", "output", //
        "backup", "backups", "tmp", "temp", ".tmp", //
        ".DS_Store", "Thumbs.db", //
        "node_modules", "dist", //
        ".cache", "__pycache__", //
        "*.dcu", "*.dcp", "*.dpu", "*.exe",
    ];
    // Python set literal còn: "*.dll", "*.bpl" — tổng 32 phần tử.
    const IGNORE_PATTERNS_EXTRA: [&str; 2] = ["*.dll", "*.bpl"];

    if IGNORE_PATTERNS.contains(&dir_name) || IGNORE_PATTERNS_EXTRA.contains(&dir_name) {
        return true;
    }
    if dir_name.ends_with(".~") || dir_name.ends_with(".swp") || dir_name.ends_with(".swo") {
        return true;
    }
    matches_extra_ignore(dir_name)
}

/// `_scan_delphi_files` — walk root, lọc dir/file, trả sorted list path string.
pub fn scan_delphi_files(root: &Path) -> Vec<String> {
    let mut files: Vec<String> = Vec::new();
    walk_delphi(root, &mut files);
    files.sort();
    files
}

fn walk_delphi(dir: &Path, files: &mut Vec<String>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut subdirs: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        let Ok(file_type) = entry.file_type() else { continue };
        if file_type.is_dir() {
            if !should_ignore_directory(&name) {
                subdirs.push(path);
            }
            continue;
        }
        let lower = name.to_lowercase();
        // Skip compiled + IDE backup.
        if [".dcu", ".dcp", ".dpu", ".exe", ".dll", ".bpl", ".~"]
            .iter()
            .any(|ext| lower.ends_with(ext))
        {
            continue;
        }
        if name.ends_with(".swp") || name.ends_with(".swo") || name.ends_with(".local") {
            continue;
        }
        if name == ".DS_Store" || name == "Thumbs.db" {
            continue;
        }
        if lower.ends_with(".pas") || lower.ends_with(".dpr") || lower.ends_with(".inc") {
            files.push(path.to_string_lossy().to_string());
        }
    }
    // os.walk topdown: duyệt con theo thứ tự readdir (thứ tự cuối cùng do sort).
    for sub in subdirs {
        walk_delphi(&sub, files);
    }
}

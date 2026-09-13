//! Port `vb_common.py::parse_vb_file` — parser LINE/REGEX thuần dùng chung 4
//! dialect. Python có thử tree-sitter nhưng CHỈ để tính `has_error`/`error_nodes`
//! cho `parse_meta` (không bao giờ vào graph rows hay `[SCAN_RESULT]`) nên
//! Rust bỏ tree-sitter, giữ `has_error=false` (divergence đã ghi nhận).
//!
//! Các quirk Python được giữ NGUYÊN (byte-parity):
//! * `_CALL_KEYWORDS` lọc callee thường;
//! * `Variable` trùng tên keyword (vd `Private Sub Foo()` sinh variable
//!   "Sub" vì `_VAR_DECL_RE` match prefix);
//! * heuristic tìm line number bằng `source.find(line, start - len(line))`
//!   (thường rơi về dòng trống đầu tiên của file hoặc 1);
//! * `type_stack`/`ns_stack` đã rỗng lúc extract properties/events/... nên
//!   `class_name`/`namespace_name` luôn None;
//! * `End Interface`/`End Enum` vừa sinh Class row (qua type stack) vừa sinh
//!   Interface/Enum row riêng.

use std::collections::HashMap;
use std::path::Path;
use std::sync::LazyLock;

use regex::Regex;

use crate::model::*;
use crate::pycompat::{
    decode_utf8_ignore, py_find_chars, py_json_dumps_pair_list, py_json_dumps_str_list,
    py_splitlines_str,
};
use cortex_analyzer_framework::scan::rel_posix;

pub const PARSE_CACHE_VERSION: &str = "vb-family-v2026-04-03-2";

static NAMESPACE_START_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^\s*(?:Public\s+|Private\s+|Friend\s+)?Namespace\s+([A-Za-z_][A-Za-z0-9_.]*)\b")
        .unwrap()
});
static NAMESPACE_END_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^\s*End\s+Namespace\b").unwrap());
static TYPE_START_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?i)^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+|Partial\s+|Static\s+|Shadows\s+|",
        r"Default\s+|NotInheritable\s+|MustInherit\s+|Global\s+)*",
        r"(Class|Module|Structure|Interface|Enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b"
    ))
    .unwrap()
});
static TYPE_END_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^\s*End\s+(Class|Module|Structure|Interface|Enum)\b").unwrap());
static FUNC_START_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?i)^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+|Static\s+|Shared\s+|Overloads\s+|",
        r"Overrides\s+|Overridable\s+|NotOverridable\s+|MustOverride\s+|Partial\s+|Default\s+|",
        r"Async\s+|Iterator\s+|Shadows\s+|Global\s+)*",
        r"(Sub|Function|Property\s+Get|Property\s+Set|Property\s+Let)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\((.*?)\))?"
    ))
    .unwrap()
});
static FUNC_END_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^\s*End\s+(Sub|Function|Property)\b").unwrap());
static IMPORTS_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^\s*Imports\s+([A-Za-z_][A-Za-z0-9_.]*)\b").unwrap());
static CALL_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(").unwrap());
static CALL_KEYWORDS: [&str; 11] = [
    "if",
    "while",
    "for",
    "select",
    "return",
    "cint",
    "cstr",
    "cdbl",
    "ctype",
    "directcast",
    "trycast",
];

static PROPERTY_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?im)^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+|ReadOnly\s+|WriteOnly\s+|",
        r"Overrides\s+|Overridable\s+|MustOverride\s+|Default\s+|Shared\s+)*",
        r"Property\s+(?P<kind>Get|Set|Let)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\((?P<params>.*?)\))?",
        r"(?:\s+As\s+(?P<type>[A-Za-z0-9_.<>]+))?"
    ))
    .unwrap()
});

static EVENT_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?im)^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+|Shared\s+|Overrides\s+|Overridable\s+|Custom\s+)*",
        r"Event\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\((?P<params>.*?)\))?"
    ))
    .unwrap()
});

static VAR_DECL_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?im)^\s*(?P<scope>Public|Private|Friend|Protected|Global|Shared|Static|Dim|Const)\s+",
        r"(?P<with_events>WithEvents\s+)?",
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\((?:\d+|\s*(?:To)?\s*\d+(?:\s*,\s*\d+)*)\))?\s*",
        r"(?:As\s+(?P<type>[A-Za-z0-9_.()]+))?",
        r"(?:\s*=\s*(?P<init>[^'\n]+))?"
    ))
    .unwrap()
});

// LƯU Ý: Python định nghĩa `_ARRAY_DECL_RE` nhưng KHÔNG dùng trong
// `parse_vb_file` (dead regex) — giữ định nghĩa cho đủ + `#[allow(dead_code)]`.
#[allow(dead_code)]
static ARRAY_DECL_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?im)^\s*(?P<scope>Public|Private|Friend|Protected|Global|Shared|Static|Dim)\s+",
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<size>[^)]+)\)\s*",
        r"(?:As\s+(?P<type>[A-Za-z0-9_.()]+))?"
    ))
    .unwrap()
});

static INTERFACE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?im)^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+|Partial\s+)*",
        r"Interface\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\s+(Inherits\s+(?P<bases>[A-Za-z0-9_,\s]+)))?"
    ))
    .unwrap()
});

static ENUM_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?im)^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+)*",
        r"Enum\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"
    ))
    .unwrap()
});

static CONST_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?im)^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+|Global\s+)*",
        r"Const\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:As\s+(?P<type>[A-Za-z0-9_]+))?\s*=\s*(?P<value>[^'\n]+)"
    ))
    .unwrap()
});

static END_PROP_SEARCH_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^\s*End\s+Property\b").unwrap());
static END_IFACE_SEARCH_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^\s*End\s+Interface\b").unwrap());
static END_ENUM_SEARCH_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^\s*End\s+Enum\b").unwrap());
static ENUM_MEMBER_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*([^'\n]+))?").unwrap());

/// `_extract_file_comment` — khối comment đầu file (dòng `'`; dừng ở dòng
/// non-comment đầu sau khi có nội dung; bỏ qua blank đầu file).
fn extract_file_comment(lines: &[String]) -> String {
    let mut parts: Vec<String> = Vec::new();
    for raw in lines {
        let stripped = raw.trim();
        if stripped.is_empty() {
            if !parts.is_empty() {
                break;
            }
            continue;
        }
        if stripped.starts_with('\'') {
            parts.push(stripped.to_string());
            continue;
        }
        break;
    }
    parts.join("\n")
}

/// `_build_note` — `Summary:\n...\n\nComment:\n...\n\nCode:\n...`.
fn build_note(code: &str, comment: &str, summary: &str) -> String {
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

/// `_normalize_callee` — bỏ whitespace, `?.` → `.`, strip `.` hai đầu.
pub fn normalize_callee(text: &str) -> String {
    let mut cleaned: String = text.chars().filter(|c| !c.is_whitespace()).collect();
    cleaned = cleaned.replace("?.", ".");
    cleaned
        .trim_matches('.')
        .to_string()
}

/// `_split_params` — tách tham số theo dấu phẩy top-level, tôn trọng quote
/// và depth `([`/`)]`.
pub fn split_params(text: &str) -> Vec<String> {
    let raw = text.trim();
    if raw.is_empty() {
        return Vec::new();
    }
    let mut chunks: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut depth = 0i32;
    let mut in_string = false;
    let mut quote = ' ';
    for ch in raw.chars() {
        if in_string {
            current.push(ch);
            if ch == quote {
                in_string = false;
            }
            continue;
        }
        if ch == '"' || ch == '\'' {
            in_string = true;
            quote = ch;
            current.push(ch);
            continue;
        }
        if ch == '(' || ch == '[' {
            depth += 1;
            current.push(ch);
            continue;
        }
        if ch == ')' || ch == ']' {
            depth = (depth - 1).max(0);
            current.push(ch);
            continue;
        }
        if ch == ',' && depth == 0 {
            let token = current.trim().to_string();
            if !token.is_empty() {
                chunks.push(token);
            }
            current = String::new();
            continue;
        }
        current.push(ch);
    }
    let token = current.trim().to_string();
    if !token.is_empty() {
        chunks.push(token);
    }
    chunks
}

fn guess_arity(param_text: &str) -> i64 {
    split_params(param_text).len() as i64
}

/// `_strip_inline_comment` — cắt từ `'` đầu tiên ngoài double-quote.
fn strip_inline_comment(line: &str) -> String {
    let mut out = String::new();
    let mut in_str = false;
    for ch in line.chars() {
        if ch == '"' {
            in_str = !in_str;
            out.push(ch);
            continue;
        }
        if ch == '\'' && !in_str {
            break;
        }
        out.push(ch);
    }
    out
}

fn join_dot(parts: &[&str]) -> String {
    parts
        .iter()
        .filter(|p| !p.is_empty())
        .cloned()
        .collect::<Vec<_>>()
        .join(".")
}

/// `_line_slice` — join lines[start-1:end] (1-based, clamp như Python slice).
fn line_slice(lines: &[String], start_line: i64, end_line: i64) -> String {
    if lines.is_empty() {
        return String::new();
    }
    let start = start_line.max(1);
    let end = start.max(end_line);
    let s = ((start - 1) as usize).min(lines.len());
    let e = (end as usize).min(lines.len());
    lines[s..e.max(s)].join("\n")
}

#[derive(Debug, Clone)]
struct OpenFunc {
    kind: String,
    name: String,
    arity: i64,
    start_line: i64,
    namespace_name: Option<String>,
    class_name: Option<String>,
}

fn stack_join(stack: &[(String, i64)]) -> Option<String> {
    if stack.is_empty() {
        None
    } else {
        Some(
            stack
                .iter()
                .map(|(name, _)| name.clone())
                .collect::<Vec<_>>()
                .join("."),
        )
    }
}

/// `".".join(item[0] for item in type_stack) if type_stack else None`
fn type_stack_name(stack: &[(String, String, i64)]) -> Option<String> {
    if stack.is_empty() {
        None
    } else {
        Some(
            stack
                .iter()
                .map(|(name, _, _)| name.clone())
                .collect::<Vec<_>>()
                .join("."),
        )
    }
}

/// Heuristic tìm line number của match — replica CHÍNH XÁC vòng lặp Python:
/// `for i, line in enumerate(lines): if source.find(line, m - len(line)) == m - len(line): break`.
/// (thường rơi về dòng trống đầu tiên của file, hoặc 1 nếu không có dòng trống)
fn python_line_number(
    source_chars: &[char],
    lines: &[String],
    line_start_chars: &[i64],
    match_start_char: i64,
) -> i64 {
    for (i, line) in lines.iter().enumerate() {
        let line_chars: Vec<char> = line.chars().collect();
        let start = match_start_char - line_chars.len() as i64;
        if py_find_chars(source_chars, &line_chars, start) == start {
            return i as i64 + 1;
        }
    }
    let _ = line_start_chars;
    1
}

/// `parse_vb_file` — trả payload đầy đủ; Err chỉ khi đọc file lỗi (caller
/// skip file như Python `_parse_single_file` bắt Exception trả None).
pub fn parse_vb_file(
    abs_path: &Path,
    root: &Path,
    dialect: &str,
    vbnet_semantic: &str,
    fallback_reason: &str,
) -> Result<FilePayload, String> {
    let source_bytes = std::fs::read(abs_path).map_err(|e| format!("{}: {e}", abs_path.display()))?;
    let source = decode_utf8_ignore(&source_bytes);
    let lines = py_splitlines_str(&source);
    let rel_path = rel_posix(root, abs_path);

    let mut payload = FilePayload::default();

    // imports
    let mut imports: Vec<String> = Vec::new();
    for raw in &lines {
        if let Some(m) = IMPORTS_RE.captures(raw) {
            imports.push(m[1].to_string());
        }
    }

    let mut ns_stack: Vec<(String, i64)> = Vec::new();
    let mut type_stack: Vec<(String, String, i64)> = Vec::new();
    let mut func_stack: Vec<OpenFunc> = Vec::new();

    // char offsets chuẩn bị cho heuristic line number (Python str indices)
    let source_chars: Vec<char> = source.chars().collect();
    let mut line_start_chars: Vec<i64> = Vec::with_capacity(lines.len());
    {
        let mut offset = 0i64;
        for line in &lines {
            line_start_chars.push(offset);
            offset += line.chars().count() as i64 + 1; // +1 terminator
        }
    }
    let match_char_start = |byte_start: usize| -> i64 { source[..byte_start].chars().count() as i64 };

    for (idx0, raw) in lines.iter().enumerate() {
        let idx = idx0 as i64 + 1;
        let stripped = raw.trim();
        if stripped.is_empty() {
            continue;
        }

        // End Sub/Function/Property → đóng function (LIFO) + extract calls
        if FUNC_END_RE.is_match(stripped) && !func_stack.is_empty() {
            let open_func = func_stack.pop().unwrap();
            let start_line = open_func.start_line;
            let end_line = idx;
            let code = line_slice(&lines, start_line, end_line);
            let name = open_func.name.clone();
            let kind = open_func.kind.clone();
            let class_name = open_func.class_name.clone();
            let namespace_name = open_func.namespace_name.clone();
            let arity = open_func.arity;

            let qualified = join_dot(&[
                namespace_name.as_deref().unwrap_or(""),
                class_name.as_deref().unwrap_or(""),
                name.as_str(),
            ]);
            let symbol_id = if qualified.is_empty() {
                format!("{name}/{arity}@{rel_path}")
            } else {
                format!("{qualified}/{arity}@{rel_path}")
            };
            let note = build_note(&code, "", "");
            payload.functions.push(FunctionDef {
                symbol_id: symbol_id.clone(),
                qualified_name: qualified.clone(),
                name,
                kind,
                class_name,
                namespace_name: namespace_name.clone(),
                file_path: rel_path.clone(),
                start_line,
                end_line,
                arity,
                code,
                comment: String::new(),
                summary: String::new(),
                note,
            });

            // call sites trong body (bao gồm cả dòng khai báo + End line)
            let s = (start_line as usize - 1).min(lines.len());
            let e = (end_line as usize).min(lines.len());
            let body_lines = &lines[s..e.max(s)];
            for (offset, body_raw) in body_lines.iter().enumerate() {
                let no_comment = strip_inline_comment(body_raw);
                if no_comment.trim().is_empty() {
                    continue;
                }
                for m in CALL_RE.captures_iter(&no_comment) {
                    let candidate = normalize_callee(&m[1]);
                    if candidate.is_empty() {
                        continue;
                    }
                    let simple = candidate
                        .rsplit('.')
                        .next()
                        .unwrap_or(&candidate)
                        .to_lowercase();
                    if CALL_KEYWORDS.contains(&simple.as_str()) {
                        continue;
                    }
                    let caller_scope = if qualified.contains('.') {
                        qualified
                            .rsplit_once('.')
                            .map(|(head, _)| head.to_string())
                    } else {
                        namespace_name.clone()
                    };
                    payload.calls.push(CallEdge {
                        caller_id: symbol_id.clone(),
                        caller_scope,
                        callee_name: candidate,
                        callee_id: None,
                        callee_arity: None,
                        call_line: start_line + offset as i64,
                    });
                }
            }
            continue;
        }

        // End Class/Module/Structure/Interface/Enum → ClassDef
        if TYPE_END_RE.is_match(stripped) && !type_stack.is_empty() {
            let (type_name, type_kind, start_line) = type_stack.pop().unwrap();
            let end_line = idx;
            let namespace_name = stack_join(&ns_stack);
            let code = line_slice(&lines, start_line, end_line);
            let qualified = join_dot(&[namespace_name.as_deref().unwrap_or(""), type_name.as_str()]);
            let class_id = if qualified.is_empty() {
                type_name.clone()
            } else {
                qualified.clone()
            };
            let note = build_note(&code, "", "");
            payload.classes.push(ClassDef {
                symbol_id: class_id,
                qualified_name: qualified,
                name: type_name,
                kind: type_kind.to_lowercase(),
                namespace_name,
                file_path: rel_path.clone(),
                start_line,
                end_line,
                code,
                comment: String::new(),
                summary: String::new(),
                note,
            });
            continue;
        }

        // End Namespace → NamespaceDef
        if NAMESPACE_END_RE.is_match(stripped) && !ns_stack.is_empty() {
            let (ns_name, start_line) = ns_stack.pop().unwrap();
            let end_line = idx;
            let code = line_slice(&lines, start_line, end_line);
            let parent = ns_stack
                .iter()
                .map(|(n, _)| n.clone())
                .collect::<Vec<_>>()
                .join(".");
            let qualified = join_dot(&[parent.as_str(), ns_name.as_str()]);
            let note = build_note(&code, "", "");
            payload.namespaces.push(NamespaceDef {
                symbol_id: format!("namespace::{qualified}@{rel_path}"),
                qualified_name: qualified,
                name: ns_name,
                file_path: rel_path.clone(),
                start_line,
                end_line,
                code,
                comment: String::new(),
                summary: String::new(),
                note,
            });
            continue;
        }

        if let Some(m) = NAMESPACE_START_RE.captures(stripped) {
            ns_stack.push((m[1].to_string(), idx));
            continue;
        }

        if let Some(m) = TYPE_START_RE.captures(stripped) {
            type_stack.push((m[2].to_string(), m[1].to_string(), idx));
            continue;
        }

        if let Some(m) = FUNC_START_RE.captures(stripped) {
            let kind = m[1].to_lowercase();
            let name = m[2].to_string();
            let params = m.get(3).map(|p| p.as_str()).unwrap_or("");
            let namespace_name = stack_join(&ns_stack);
            let class_name = type_stack_name(&type_stack);
            func_stack.push(OpenFunc {
                kind,
                name,
                arity: guess_arity(params),
                start_line: idx,
                namespace_name,
                class_name,
            });
            continue;
        }
    }

    // close unbalanced blocks — KHÔNG extract calls (như Python)
    let end_line = if lines.is_empty() { 1 } else { lines.len() as i64 };
    while let Some(open_func) = func_stack.pop() {
        let start_line = open_func.start_line;
        let code = line_slice(&lines, start_line, end_line);
        let name = open_func.name.clone();
        let kind = open_func.kind.clone();
        let class_name = open_func.class_name.clone();
        let namespace_name = open_func.namespace_name.clone();
        let arity = open_func.arity;
        let qualified = join_dot(&[
            namespace_name.as_deref().unwrap_or(""),
            class_name.as_deref().unwrap_or(""),
            name.as_str(),
        ]);
        let symbol_id = if qualified.is_empty() {
            format!("{name}/{arity}@{rel_path}")
        } else {
            format!("{qualified}/{arity}@{rel_path}")
        };
        let note = build_note(&code, "", "");
        payload.functions.push(FunctionDef {
            symbol_id,
            qualified_name: qualified,
            name,
            kind,
            class_name,
            namespace_name,
            file_path: rel_path.clone(),
            start_line,
            end_line,
            arity,
            code,
            comment: String::new(),
            summary: String::new(),
            note,
        });
    }

    while let Some((type_name, type_kind, start_line)) = type_stack.pop() {
        let namespace_name = stack_join(&ns_stack);
        let code = line_slice(&lines, start_line, end_line);
        let qualified = join_dot(&[namespace_name.as_deref().unwrap_or(""), type_name.as_str()]);
        let class_id = if qualified.is_empty() {
            type_name.clone()
        } else {
            qualified.clone()
        };
        let note = build_note(&code, "", "");
        payload.classes.push(ClassDef {
            symbol_id: class_id,
            qualified_name: qualified,
            name: type_name,
            kind: type_kind.to_lowercase(),
            namespace_name,
            file_path: rel_path.clone(),
            start_line,
            end_line,
            code,
            comment: String::new(),
            summary: String::new(),
            note,
        });
    }

    while let Some((ns_name, start_line)) = ns_stack.pop() {
        let parent = ns_stack
            .iter()
            .map(|(n, _)| n.clone())
            .collect::<Vec<_>>()
            .join(".");
        let qualified = join_dot(&[parent.as_str(), ns_name.as_str()]);
        let code = line_slice(&lines, start_line, end_line);
        let note = build_note(&code, "", "");
        payload.namespaces.push(NamespaceDef {
            symbol_id: format!("namespace::{qualified}@{rel_path}"),
            qualified_name: qualified,
            name: ns_name,
            file_path: rel_path.clone(),
            start_line,
            end_line,
            code,
            comment: String::new(),
            summary: String::new(),
            note,
        });
    }

    // ── Properties ──────────────────────────────────────────────────────────
    // (type_stack/ns_stack đã rỗng tại đây — class_name/namespace_name None)
    for m in PROPERTY_RE.captures_iter(&source) {
        let kind = m["kind"].to_lowercase();
        let name = m["name"].to_string();
        let params = m.name("params").map(|p| p.as_str()).unwrap_or("");
        let return_type = m.name("type").map(|p| p.as_str()).unwrap_or("");

        let match_start = match_char_start(m.get(0).unwrap().start());
        let line_num = python_line_number(&source_chars, &lines, &line_start_chars, match_start);

        let mut end_line_num = line_num;
        for (i, line) in lines.iter().enumerate().skip(line_num.max(0) as usize) {
            if END_PROP_SEARCH_RE.is_match(line) {
                end_line_num = i as i64 + 1;
                break;
            }
        }

        let qualified = name.clone();
        let symbol_id = format!("{qualified}@{rel_path}");
        let code = if end_line_num > line_num {
            line_slice(&lines, line_num, end_line_num)
        } else {
            m[0].to_string()
        };
        let note = build_note(&code, "", "");
        payload.properties.push(PropertyDef {
            symbol_id,
            qualified_name: qualified,
            name,
            kind,
            class_name: None,
            namespace_name: None,
            file_path: rel_path.clone(),
            start_line: line_num,
            end_line: end_line_num,
            parameters: params.to_string(),
            return_type: return_type.to_string(),
            code,
            comment: String::new(),
            summary: String::new(),
            note,
        });
    }

    // ── Events ──────────────────────────────────────────────────────────────
    for m in EVENT_RE.captures_iter(&source) {
        let name = m["name"].to_string();
        let params = m.name("params").map(|p| p.as_str()).unwrap_or("");

        let match_start = match_char_start(m.get(0).unwrap().start());
        let line_num = python_line_number(&source_chars, &lines, &line_start_chars, match_start);

        let qualified = name.clone();
        let symbol_id = format!("{qualified}@{rel_path}");
        let code = m[0].to_string();
        let note = build_note(&code, "", "");
        payload.events.push(EventDef {
            symbol_id,
            qualified_name: qualified,
            name,
            class_name: None,
            namespace_name: None,
            file_path: rel_path.clone(),
            start_line: line_num,
            end_line: line_num,
            parameters: params.to_string(),
            code,
            comment: String::new(),
            summary: String::new(),
            note,
        });
    }

    // ── Interfaces ──────────────────────────────────────────────────────────
    for m in INTERFACE_RE.captures_iter(&source) {
        let name = m["name"].to_string();
        let bases = m.name("bases").map(|p| p.as_str()).unwrap_or("");

        let match_start = match_char_start(m.get(0).unwrap().start());
        let line_num = python_line_number(&source_chars, &lines, &line_start_chars, match_start);

        let mut end_line_num = line_num;
        for (i, line) in lines.iter().enumerate().skip(line_num.max(0) as usize) {
            if END_IFACE_SEARCH_RE.is_match(line) {
                end_line_num = i as i64 + 1;
                break;
            }
        }

        let qualified = name.clone();
        let symbol_id = format!("{qualified}@{rel_path}");
        let base_list: Vec<String> = if bases.is_empty() {
            Vec::new()
        } else {
            bases
                .split(',')
                .map(|b| b.trim().to_string())
                .filter(|b| !b.is_empty())
                .collect()
        };
        let code = if end_line_num > line_num {
            line_slice(&lines, line_num, end_line_num)
        } else {
            m[0].to_string()
        };
        let note = build_note(&code, "", "");
        payload.interfaces.push(InterfaceDef {
            symbol_id,
            qualified_name: qualified,
            name,
            namespace_name: None,
            file_path: rel_path.clone(),
            start_line: line_num,
            end_line: end_line_num,
            base_interfaces: base_list,
            code,
            comment: String::new(),
            summary: String::new(),
            note,
        });
    }

    // ── Enums ───────────────────────────────────────────────────────────────
    for m in ENUM_RE.captures_iter(&source) {
        let name = m["name"].to_string();

        let match_start = match_char_start(m.get(0).unwrap().start());
        let line_num = python_line_number(&source_chars, &lines, &line_start_chars, match_start);

        let mut end_line_num = line_num;
        let mut members: Vec<(String, String)> = Vec::new();
        for (i, line) in lines.iter().enumerate().skip(line_num.max(0) as usize) {
            if END_ENUM_SEARCH_RE.is_match(line) {
                end_line_num = i as i64 + 1;
                break;
            }
            if let Some(mm) = ENUM_MEMBER_RE.captures(line)
                && i as i64 > line_num
            {
                let member_name = mm[1].to_string();
                let member_value = mm.get(2).map(|v| v.as_str()).unwrap_or("").to_string();
                members.push((member_name, member_value));
            }
        }

        let qualified = name.clone();
        let symbol_id = format!("{qualified}@{rel_path}");
        let code = if end_line_num > line_num {
            line_slice(&lines, line_num, end_line_num)
        } else {
            m[0].to_string()
        };
        let note = build_note(&code, "", "");
        payload.enums.push(EnumDef {
            symbol_id,
            qualified_name: qualified,
            name,
            namespace_name: None,
            class_name: None,
            file_path: rel_path.clone(),
            start_line: line_num,
            end_line: end_line_num,
            members,
            code,
            comment: String::new(),
            summary: String::new(),
            note,
        });
    }

    // ── Constants ───────────────────────────────────────────────────────────
    for m in CONST_RE.captures_iter(&source) {
        let name = m["name"].to_string();
        let value = m["value"].trim().to_string();
        let type_name = m.name("type").map(|p| p.as_str()).unwrap_or("");

        let match_start = match_char_start(m.get(0).unwrap().start());
        let line_num = python_line_number(&source_chars, &lines, &line_start_chars, match_start);

        let qualified = name.clone();
        let symbol_id = format!("{qualified}@{rel_path}");
        let code = m[0].to_string();
        let note = build_note(&code, "", "");
        payload.constants.push(ConstantDef {
            symbol_id,
            qualified_name: qualified,
            name,
            value,
            type_name: type_name.to_string(),
            class_name: None,
            namespace_name: None,
            file_path: rel_path.clone(),
            line_number: line_num,
            code,
            comment: String::new(),
            summary: String::new(),
            note,
        });
    }

    // ── Variables ───────────────────────────────────────────────────────────
    for m in VAR_DECL_RE.captures_iter(&source) {
        let scope = m["scope"].to_lowercase();
        let name = m["name"].to_string();
        let type_name = m.name("type").map(|p| p.as_str()).unwrap_or("Variant");
        // Python: match.group("with_events") không dùng — giữ nguyên hành vi.

        let match_start = match_char_start(m.get(0).unwrap().start());
        let line_num = python_line_number(&source_chars, &lines, &line_start_chars, match_start);

        let is_global = matches!(scope.as_str(), "public" | "global" | "friend");
        let is_shared = scope == "shared";

        let qualified = name.clone();
        let symbol_id = format!("{qualified}@{rel_path}");
        let code = m[0].to_string();
        let note = build_note(&code, "", "");
        payload.variables.push(VariableDef {
            symbol_id,
            qualified_name: qualified,
            name,
            type_name: type_name.to_string(),
            is_global,
            is_shared,
            class_name: None,
            namespace_name: None,
            file_path: rel_path.clone(),
            line_number: line_num,
            code,
            comment: String::new(),
            summary: String::new(),
            note,
        });
    }

    // ── Array declarations ──────────────────────────────────────────────────
    // LƯU Ý: Python định nghĩa `_ARRAY_DECL_RE` nhưng KHÔNG BAO GIỜ dùng
    // (dead regex) — "Dim arr(10) As Integer" chỉ sinh 1 Variable row qua
    // `_VAR_DECL_RE` (pattern chấp nhận mảng "(10)"). Không port loop chết.

    let file_comment = extract_file_comment(&lines);
    let file_summary = file_comment.clone();
    let note = build_note(&source, &file_comment, &file_summary);
    payload.file_def = Some(FileDef {
        file_path: rel_path.clone(),
        start_line: 1,
        end_line,
        code: source.clone(),
        comment: file_comment,
        summary: file_summary,
        note,
        imports: Some(imports),
        exports: Some(Vec::new()),
    });

    payload.parse_meta = serde_json::json!({
        "parser_language": format!("{dialect}_tree_sitter"),
        "parse_cache_version": PARSE_CACHE_VERSION,
        "has_error": false,
        "error_nodes": 0,
        "line_count": end_line,
        "parser_engine": "regex",
        "semantic_mode": if dialect == "vbnet" { vbnet_semantic } else { "off" },
        "semantic_enabled": false,
        "fallback_reason": fallback_reason,
        "worker_elapsed_ms": 0,
        "workspace_kind": "none",
        "solution_or_project_path": "",
        "semantic_errors": [],
        "resolution_source": "syntax",
        "requested_engine": if dialect == "vbnet" { "auto" } else { "regex" },
    });

    Ok(payload)
}

/// `resolve_calls` — qualified exact (lowercase) → simple-name candidates
/// (sorted, lấy phần tử đầu). Ghi đè cả callee_id semantic của Roslyn như
/// Python (`if target: call.callee_id = target`).
pub fn resolve_calls(payloads: &mut [FilePayload]) {
    let mut by_qualified: HashMap<String, String> = HashMap::new();
    let mut by_simple: HashMap<String, Vec<String>> = HashMap::new();
    for payload in payloads.iter() {
        for func in &payload.functions {
            let q_key = func.qualified_name.trim().to_lowercase();
            if !q_key.is_empty() {
                by_qualified.insert(q_key, func.symbol_id.clone());
            }
            let simple = func.name.trim().to_lowercase();
            if simple.is_empty() {
                continue;
            }
            by_simple.entry(simple).or_default().push(func.symbol_id.clone());
        }
    }

    for payload in payloads.iter_mut() {
        for call in &mut payload.calls {
            let raw = call.callee_name.trim().to_string();
            if raw.is_empty() {
                continue;
            }
            let q_key = raw.to_lowercase();
            let mut target = by_qualified.get(&q_key).cloned();
            if target.is_none() {
                let simple = raw
                    .rsplit('.')
                    .next()
                    .unwrap_or(&raw)
                    .to_lowercase();
                if let Some(candidates) = by_simple.get(&simple)
                    && !candidates.is_empty()
                {
                    let mut sorted_candidates = candidates.clone();
                    sorted_candidates.sort();
                    target = Some(sorted_candidates[0].clone());
                }
            }
            if let Some(t) = target {
                call.callee_id = Some(t);
            }
        }
    }
}

// ── Row builders (`asdict_*`) ───────────────────────────────────────────────

pub type Row = serde_json::Map<String, serde_json::Value>;

fn scope_name(parts: &[&Option<String>]) -> String {
    parts
        .iter()
        .filter_map(|p| p.as_deref())
        .filter(|p| !p.is_empty())
        .collect::<Vec<&str>>()
        .join(".")
}

pub fn asdict_function(
    func: &FunctionDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let scope = scope_name(&[&func.namespace_name, &func.class_name]);
    serde_json::json!({
        "id": func.symbol_id,
        "name": func.name,
        "qualified_name": func.qualified_name,
        "kind": func.kind,
        "scope_name": scope,
        "class_name": func.class_name,
        "package_name": func.namespace_name,
        "file_path": func.file_path,
        "start_line": func.start_line,
        "end_line": func.end_line,
        "arity": func.arity,
        "code": func.code,
        "comment": func.comment,
        "summary": func.summary,
        "note": func.note,
        "exported": false,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    })
    .as_object()
    .unwrap()
    .clone()
}

pub fn asdict_class(
    cls: &ClassDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    serde_json::json!({
        "id": cls.symbol_id,
        "name": cls.name,
        "qualified_name": cls.qualified_name,
        "kind": cls.kind,
        "file_path": cls.file_path,
        "start_line": cls.start_line,
        "end_line": cls.end_line,
        "code": cls.code,
        "comment": cls.comment,
        "summary": cls.summary,
        "note": cls.note,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    })
    .as_object()
    .unwrap()
    .clone()
}

pub fn asdict_namespace(
    ns: &NamespaceDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    serde_json::json!({
        "id": ns.symbol_id,
        "name": ns.name,
        "qualified_name": ns.qualified_name,
        "file_path": ns.file_path,
        "start_line": ns.start_line,
        "end_line": ns.end_line,
        "code": ns.code,
        "comment": ns.comment,
        "summary": ns.summary,
        "note": ns.note,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    })
    .as_object()
    .unwrap()
    .clone()
}

pub fn asdict_file(
    file_def: &FileDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    serde_json::json!({
        "id": file_def.file_path,
        "path": file_def.file_path,
        "start_line": file_def.start_line,
        "end_line": file_def.end_line,
        "code": file_def.code,
        "comment": file_def.comment,
        "summary": file_def.summary,
        "note": file_def.note,
        "imports": file_def.imports.clone().unwrap_or_default(),
        "exports": file_def.exports.clone().unwrap_or_default(),
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    })
    .as_object()
    .unwrap()
    .clone()
}

pub fn asdict_property(
    prop: &PropertyDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let scope = scope_name(&[&prop.namespace_name, &prop.class_name]);
    serde_json::json!({
        "id": prop.symbol_id,
        "name": prop.name,
        "qualified_name": prop.qualified_name,
        "kind": format!("property_{}", prop.kind),
        "scope_name": scope,
        "class_name": prop.class_name,
        "package_name": prop.namespace_name,
        "file_path": prop.file_path,
        "start_line": prop.start_line,
        "end_line": prop.end_line,
        "parameters": prop.parameters,
        "return_type": prop.return_type,
        "code": prop.code,
        "comment": prop.comment,
        "summary": prop.summary,
        "note": prop.note,
        "exported": false,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    })
    .as_object()
    .unwrap()
    .clone()
}

pub fn asdict_event(
    event: &EventDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let scope = scope_name(&[&event.namespace_name, &event.class_name]);
    serde_json::json!({
        "id": event.symbol_id,
        "name": event.name,
        "qualified_name": event.qualified_name,
        "kind": "event",
        "scope_name": scope,
        "class_name": event.class_name,
        "package_name": event.namespace_name,
        "file_path": event.file_path,
        "start_line": event.start_line,
        "end_line": event.end_line,
        "parameters": event.parameters,
        "code": event.code,
        "comment": event.comment,
        "summary": event.summary,
        "note": event.note,
        "exported": false,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    })
    .as_object()
    .unwrap()
    .clone()
}

pub fn asdict_interface(
    iface: &InterfaceDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    serde_json::json!({
        "id": iface.symbol_id,
        "name": iface.name,
        "qualified_name": iface.qualified_name,
        "kind": "interface",
        "file_path": iface.file_path,
        "start_line": iface.start_line,
        "end_line": iface.end_line,
        "base_interfaces": py_json_dumps_str_list(&iface.base_interfaces),
        "code": iface.code,
        "comment": iface.comment,
        "summary": iface.summary,
        "note": iface.note,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    })
    .as_object()
    .unwrap()
    .clone()
}

pub fn asdict_enum(
    enum_def: &EnumDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let scope = scope_name(&[&enum_def.namespace_name, &enum_def.class_name]);
    serde_json::json!({
        "id": enum_def.symbol_id,
        "name": enum_def.name,
        "qualified_name": enum_def.qualified_name,
        "kind": "enum",
        "scope_name": scope,
        "class_name": enum_def.class_name,
        "package_name": enum_def.namespace_name,
        "file_path": enum_def.file_path,
        "start_line": enum_def.start_line,
        "end_line": enum_def.end_line,
        "members": py_json_dumps_pair_list(&enum_def.members),
        "code": enum_def.code,
        "comment": enum_def.comment,
        "summary": enum_def.summary,
        "note": enum_def.note,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    })
    .as_object()
    .unwrap()
    .clone()
}

pub fn asdict_constant(
    constant: &ConstantDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let scope = scope_name(&[&constant.namespace_name, &constant.class_name]);
    serde_json::json!({
        "id": constant.symbol_id,
        "name": constant.name,
        "qualified_name": constant.qualified_name,
        "kind": "constant",
        "scope_name": scope,
        "class_name": constant.class_name,
        "package_name": constant.namespace_name,
        "file_path": constant.file_path,
        "line_number": constant.line_number,
        "value": constant.value,
        "type_name": constant.type_name,
        "code": constant.code,
        "comment": constant.comment,
        "summary": constant.summary,
        "note": constant.note,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    })
    .as_object()
    .unwrap()
    .clone()
}

pub fn asdict_variable(
    var: &VariableDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let scope = scope_name(&[&var.namespace_name, &var.class_name]);
    serde_json::json!({
        "id": var.symbol_id,
        "name": var.name,
        "qualified_name": var.qualified_name,
        "kind": "variable",
        "scope_name": scope,
        "class_name": var.class_name,
        "package_name": var.namespace_name,
        "file_path": var.file_path,
        "line_number": var.line_number,
        "type_name": var.type_name,
        "is_global": var.is_global,
        "is_shared": var.is_shared,
        "code": var.code,
        "comment": var.comment,
        "summary": var.summary,
        "note": var.note,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    })
    .as_object()
    .unwrap()
    .clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_callee_strips_whitespace_and_dots() {
        assert_eq!(normalize_callee("Foo.Bar"), "Foo.Bar");
        assert_eq!(normalize_callee("Foo .Bar"), "Foo.Bar");
        assert_eq!(normalize_callee("Foo?.Bar"), "Foo.Bar");
        assert_eq!(normalize_callee(".Leading."), "Leading");
    }

    #[test]
    fn split_params_nested_and_quoted() {
        assert_eq!(split_params(""), Vec::<String>::new());
        assert_eq!(split_params("a, b"), vec!["a", "b"]);
        assert_eq!(split_params("f(1, 2), \"x,y\""), vec!["f(1, 2)", "\"x,y\""]);
        assert_eq!(split_params("ByVal x As Integer"), vec!["ByVal x As Integer"]);
    }

    #[test]
    fn parse_basic_vb_file() {
        let dir = std::env::temp_dir().join(format!("vb_parse_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("Sample.vb");
        std::fs::write(
            &file,
            "Namespace N\n    Public Class C\n        Public Sub Go()\n            Helper(1)\n        End Sub\n    End Class\nEnd Namespace\n",
        )
        .unwrap();
        let payload = parse_vb_file(&file, &dir, "vbnet", "auto", "").unwrap();
        assert_eq!(payload.namespaces.len(), 1);
        assert_eq!(payload.namespaces[0].qualified_name, "N");
        assert_eq!(payload.classes.len(), 1);
        assert_eq!(payload.classes[0].kind, "class");
        // "Public Sub Go" + self-call "Go(" (signature line nằm trong body)
        assert!(payload.functions.iter().any(|f| f.name == "Go"));
        assert_eq!(payload.functions[0].qualified_name, "N.C.Go");
        assert_eq!(payload.functions[0].symbol_id, "N.C.Go/0@Sample.vb");
        assert!(payload
            .calls
            .iter()
            .any(|c| c.callee_name == "Go" && c.callee_id.is_none()));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn var_decl_quirk_extracts_keyword_named_variables() {
        let dir = std::env::temp_dir().join(format!("vb_parse_test2_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("Quirk.vb");
        std::fs::write(&file, "Private Sub Handle(x As Integer)\nEnd Sub\n").unwrap();
        let payload = parse_vb_file(&file, &dir, "vbnet", "auto", "").unwrap();
        // "Private Sub" → variable tên "Sub" (quirk Python giữ nguyên)
        assert!(payload.variables.iter().any(|v| v.name == "Sub"));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn const_line_yields_constant_and_variable() {
        let dir = std::env::temp_dir().join(format!("vb_parse_test3_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("Consts.vb");
        std::fs::write(&file, "Private Const Max As Integer = 5\n").unwrap();
        let payload = parse_vb_file(&file, &dir, "vbnet", "auto", "").unwrap();
        assert!(payload.constants.iter().any(|c| c.name == "Max"));
        assert!(payload.variables.iter().any(|v| v.name == "Const"));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn interface_enum_dual_rows() {
        let dir = std::env::temp_dir().join(format!("vb_parse_test4_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("Types.vb");
        std::fs::write(
            &file,
            "Public Enum Mode\n    Off = 0\n    On = 1\nEnd Enum\n",
        )
        .unwrap();
        let payload = parse_vb_file(&file, &dir, "vbnet", "auto", "").unwrap();
        assert!(payload.classes.iter().any(|c| c.name == "Mode" && c.kind == "enum"));
        assert_eq!(payload.enums.len(), 1);
        // Quirk upstream GIỮ NGUYÊN: heuristic line_num trả 1 (file không dòng
        // trống) + điều kiện `i > line_num` trong vòng member (Python dùng
        // 1-based line_num làm 0-based start index) ⇒ member đầu tiên bị skip.
        assert_eq!(payload.enums[0].members, vec![("On".to_string(), "1".to_string())]);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn resolve_prefers_sorted_simple_candidate() {
        let mut payloads = vec![
            FilePayload {
                functions: vec![
                    FunctionDef {
                        symbol_id: "z.Helper/1@b.vb".into(),
                        qualified_name: "z.Helper".into(),
                        name: "Helper".into(),
                        kind: "sub".into(),
                        class_name: None,
                        namespace_name: None,
                        file_path: "b.vb".into(),
                        start_line: 1,
                        end_line: 2,
                        arity: 1,
                        code: String::new(),
                        comment: String::new(),
                        summary: String::new(),
                        note: String::new(),
                    },
                    FunctionDef {
                        symbol_id: "a.Helper/1@a.vb".into(),
                        qualified_name: "a.Helper".into(),
                        name: "Helper".into(),
                        kind: "sub".into(),
                        class_name: None,
                        namespace_name: None,
                        file_path: "a.vb".into(),
                        start_line: 1,
                        end_line: 2,
                        arity: 1,
                        code: String::new(),
                        comment: String::new(),
                        summary: String::new(),
                        note: String::new(),
                    },
                ],
                calls: vec![CallEdge {
                    caller_id: "z.Helper/1@b.vb".into(),
                    caller_scope: None,
                    callee_name: "Helper".into(),
                    callee_id: None,
                    callee_arity: None,
                    call_line: 2,
                }],
                ..Default::default()
            },
        ];
        resolve_calls(&mut payloads);
        assert_eq!(payloads[0].calls[0].callee_id.as_deref(), Some("a.Helper/1@a.vb"));
    }
}

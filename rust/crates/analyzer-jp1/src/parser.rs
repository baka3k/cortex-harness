//! JP1/AJS jobnet parser — port `tools/jp1/parser.py` + `sniff.py` +
//! `tools/common/legacy_encoding.py` (decode_legacy_bytes). Line-based,
//! regex scanning — KHÔNG dùng tree-sitter (khác cobol).

use std::collections::BTreeMap;
use std::path::Path;

use fancy_regex::Regex;
use serde_json::{json, Map, Value};

use crate::cp932_table::{CP932_PAIRS, CP932_SINGLE};

// ── legacy_encoding.py ───────────────────────────────────────────────────

pub struct LegacyText {
    pub text: String,
    pub encoding: String,
}

#[derive(Debug)]
pub struct LegacyDecodeError;

/// `decode_legacy_bytes` — BOM → NUL heuristic → utf-8 → cp932 → cp1252
/// (errors=replace).
pub fn decode_legacy_bytes(data: &[u8]) -> Result<LegacyText, LegacyDecodeError> {
    if data.starts_with(&[0xff, 0xfe]) {
        return Ok(LegacyText {
            text: decode_utf16(&data[2..], true)?,
            encoding: "utf-16-le".to_string(),
        });
    }
    if data.starts_with(&[0xfe, 0xff]) {
        return Ok(LegacyText {
            text: decode_utf16(&data[2..], false)?,
            encoding: "utf-16-be".to_string(),
        });
    }
    if data.starts_with(&[0xef, 0xbb, 0xbf]) {
        return Ok(LegacyText {
            text: std::str::from_utf8(&data[3..])
                .map_err(|_| LegacyDecodeError)?
                .to_string(),
            encoding: "utf-8-sig".to_string(),
        });
    }

    let sample = &data[..data.len().min(512)];
    if !sample.is_empty() {
        let odd_nuls = sample[1..].iter().step_by(2).filter(|&&b| b == 0).count();
        let even_nuls = sample.iter().step_by(2).filter(|&&b| b == 0).count();
        let threshold = std::cmp::max(2, sample.len() / 8);
        if odd_nuls >= threshold {
            return Ok(LegacyText {
                text: decode_utf16(data, true)?,
                encoding: "utf-16-le".to_string(),
            });
        }
        if even_nuls >= threshold {
            return Ok(LegacyText {
                text: decode_utf16(data, false)?,
                encoding: "utf-16-be".to_string(),
            });
        }
    }

    if let Ok(text) = std::str::from_utf8(data) {
        return Ok(LegacyText {
            text: text.to_string(),
            encoding: "utf-8".to_string(),
        });
    }

    if let Some(text) = decode_cp932(data) {
        return Ok(LegacyText {
            text,
            encoding: "cp932".to_string(),
        });
    }
    Ok(LegacyText {
        text: decode_cp1252_replace(data),
        encoding: "cp1252".to_string(),
    })
}

/// `bytes.decode('utf-16-le'|'utf-16-be')` strict — cặp lẻ/unpaired surrogate
/// raise (Python UnicodeDecodeError lan lên).
fn decode_utf16(data: &[u8], little_endian: bool) -> Result<String, LegacyDecodeError> {
    if !data.len().is_multiple_of(2) {
        return Err(LegacyDecodeError);
    }
    let mut units: Vec<u16> = Vec::with_capacity(data.len() / 2);
    for chunk in data.chunks_exact(2) {
        let unit = if little_endian {
            u16::from_le_bytes([chunk[0], chunk[1]])
        } else {
            u16::from_be_bytes([chunk[0], chunk[1]])
        };
        units.push(unit);
    }
    let mut out = String::with_capacity(units.len());
    let mut iter = units.into_iter();
    while let Some(unit) = iter.next() {
        if (0xd800..0xdc00).contains(&unit) {
            // high surrogate → cần low surrogate kế tiếp
            let next = iter.next().ok_or(LegacyDecodeError)?;
            if !(0xdc00..0xe000).contains(&next) {
                return Err(LegacyDecodeError);
            }
            let combined = 0x10000 + ((unit as u32 - 0xd800) << 10) + (next as u32 - 0xdc00);
            out.push(char::from_u32(combined).ok_or(LegacyDecodeError)?);
        } else if (0xdc00..0xe000).contains(&unit) {
            return Err(LegacyDecodeError);
        } else {
            out.push(char::from_u32(unit as u32).ok_or(LegacyDecodeError)?);
        }
    }
    Ok(out)
}

/// `bytes.decode('cp932')` strict — bảng sinh từ CPython; None/Err ⇒ fall
/// qua cp1252 như Python UnicodeDecodeError.
fn decode_cp932(data: &[u8]) -> Option<String> {
    let mut out = String::with_capacity(data.len());
    let mut index = 0usize;
    while index < data.len() {
        let byte = data[index];
        if let Some(ch) = CP932_SINGLE[byte as usize] {
            out.push(ch);
            index += 1;
            continue;
        }
        // lead byte của cặp 2 byte (0x81..=0x9F, 0xE0..=0xFC, 0xA0..=0xDF cặp)
        if index + 1 >= data.len() {
            return None; // truncated pair
        }
        let trail = data[index + 1];
        let key = (u16::from(byte) << 8) | u16::from(trail);
        match CP932_PAIRS.binary_search_by_key(&key, |entry| entry.0) {
            Ok(position) => {
                out.push_str(CP932_PAIRS[position].1);
            }
            Err(_) => return None,
        }
        index += 2;
    }
    Some(out)
}

/// `data.decode('cp1252', errors='replace')` — byte undefined ⇒ U+FFFD.
fn decode_cp1252_replace(data: &[u8]) -> String {
    // Python cp1252: 5 byte undefined (81 8D 8F 90 9D) → replacement char.
    const UNDEFINED: [u8; 5] = [0x81, 0x8d, 0x8f, 0x90, 0x9d];
    data.iter()
        .map(|&byte| {
            if UNDEFINED.contains(&byte) {
                '\u{fffd}'
            } else {
                char::from_u32(byte as u32).expect("latin1 range")
            }
        })
        .collect()
}

// ── sniff.py ─────────────────────────────────────────────────────────────

fn unit_line_regex() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^unit=").expect("regex"))
}

fn ty_line_regex() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^ty\s*=").expect("regex"))
}

/// `is_jp1_file` — .txt + content sniff trên 65536 byte đầu.
pub fn is_jp1_file(path: &str) -> bool {
    if !path.to_lowercase().ends_with(".txt") {
        return false;
    }
    let Ok(metadata) = std::fs::metadata(path) else {
        return false;
    };
    let _ = (metadata.modified(), metadata.len());
    sniff(path)
}

/// `_sniff` (bỏ lru_cache — parity không đổi).
fn sniff(path: &str) -> bool {
    let Ok(data) = std::fs::read(path) else {
        return false;
    };
    let text = match decode_legacy_bytes(&data[..data.len().min(65536)]) {
        Ok(decoded) => decoded.text,
        Err(_) => return false,
    };
    // Python: [line.strip() for line in text.splitlines() if line.strip()]
    let nonblank: Vec<&str> = py_splitlines(&text)
        .into_iter()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect();
    if !nonblank
        .iter()
        .take(5)
        .any(|line| unit_line_regex().is_match(line).unwrap_or(false))
    {
        return false;
    }
    nonblank
        .iter()
        .take(10)
        .any(|line| line.starts_with('{') || *line == "{")
        && nonblank
            .iter()
            .take(50)
            .any(|line| ty_line_regex().is_match(line).unwrap_or(false))
}

/// Python `str.splitlines()` — đầy đủ boundary semantics (dùng cho sniff +
/// parse).
pub fn py_splitlines(text: &str) -> Vec<&str> {
    let char_indices: Vec<(usize, char)> = text.char_indices().collect();
    let mut lines: Vec<&str> = Vec::new();
    let byte_len = text.len();
    let mut start = 0usize;
    let mut i = 0usize;
    while i < char_indices.len() {
        let (pos, ch) = char_indices[i];
        let is_boundary = matches!(
            ch,
            '\n' | '\r' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}'
                | '\u{2028}' | '\u{2029}'
        );
        if !is_boundary {
            i += 1;
            continue;
        }
        let crlf = ch == '\r' && char_indices.get(i + 1).map(|(_, c)| *c) == Some('\n');
        let end = if crlf {
            char_indices[i + 1].0 + 1
        } else {
            pos + ch.len_utf8()
        };
        lines.push(&text[start..pos]);
        i += if crlf { 2 } else { 1 };
        start = end;
    }
    if start < byte_len {
        lines.push(&text[start..]);
    }
    lines
}

// ── parser.py ────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct Jp1Unit {
    pub unit_id: String,
    pub name: String,
    pub file_path: String,
    pub parent_id: Option<String>,
    pub start_line: i64,
    pub end_line: i64,
    pub unit_type: String,
    pub comment: String,
    pub exec_target: String,
}

#[derive(Debug, Clone)]
pub struct Jp1Relation {
    pub source_id: String,
    pub source_label: String,
    pub target_id: String,
    pub target_label: String,
    pub rel_type: String,
    pub line: i64,
    pub raw_target: String,
    pub resolved: bool,
}

#[derive(Debug, Clone)]
pub struct Jp1Diagnostic {
    pub code: String,
    pub message: String,
    pub file_path: String,
    pub line: i64,
}

#[derive(Debug, Clone)]
pub struct Jp1File {
    pub file_path: String,
    pub encoding: String,
    pub units: Vec<Jp1Unit>,
    pub relations: Vec<Jp1Relation>,
    pub diagnostics: Vec<Jp1Diagnostic>,
}

/// `_clean` — strip → rstrip(';') → strip → strip('"').
fn clean(value: &str) -> String {
    value
        .trim()
        .trim_end_matches(';')
        .trim()
        .trim_matches('"')
        .to_string()
}

/// `_resolve_exec_target`.
pub fn resolve_exec_target(raw: &str, project_root: &Path) -> (String, bool) {
    static VAR_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let var_re = VAR_RE.get_or_init(|| Regex::new(r"@[A-Za-z_][A-Za-z0-9_]*@|\$").expect("regex"));
    if var_re.is_match(raw).unwrap_or(false) {
        return (raw.to_string(), false);
    }
    let root_real = crate::pipeline::py_realpath(project_root);
    let candidate_raw = crate::pipeline::join_py(&root_real, raw.trim_start_matches('/'));
    let candidate = crate::pipeline::py_realpath(&candidate_raw);
    let within_root = match common_path(&root_real, &candidate) {
        Some(common) => common == root_real,
        None => false,
    };
    if within_root && candidate.is_file() {
        let rel = candidate
            .strip_prefix(&root_real)
            .map(|rel| {
                rel.components()
                    .map(|c| c.as_os_str().to_string_lossy().to_string())
                    .collect::<Vec<_>>()
                    .join("/")
            })
            .unwrap_or_default();
        return (rel, true);
    }
    (raw.replace('\\', "/"), false)
}

/// `os.path.commonpath([a, b])` cho 2 absolute path — None = không có prefix
/// chung (hoặc mixed abs/rel ⇒ ValueError phía Python).
fn common_path(a: &Path, b: &Path) -> Option<std::path::PathBuf> {
    if !a.is_absolute() || !b.is_absolute() {
        return None;
    }
    let mut common = std::path::PathBuf::new();
    let mut a_components = a.components();
    let mut b_components = b.components();
    loop {
        match (a_components.next(), b_components.next()) {
            (Some(x), Some(y)) if x == y => common.push(x.as_os_str()),
            _ => break,
        }
    }
    Some(common)
}

/// `parse_jp1_text`.
pub fn parse_jp1_text(source_text: &str, file_path: &str, project_root: &Path) -> Jp1File {
    let mut units: Vec<Jp1Unit> = Vec::new();
    let mut relations: Vec<Jp1Relation> = Vec::new();
    let mut diagnostics: Vec<Jp1Diagnostic> = Vec::new();
    // Python dùng SHARED object (units/pending/stack trỏ cùng instance) —
    // mutation qua stack[-1] thấy được ở units. Rust: stack giữ INDEX.
    let mut stack: Vec<usize> = Vec::new();
    let mut pending: Option<usize> = None;
    let mut arcs: Vec<(Option<String>, String, String, i64)> = Vec::new();

    static UNIT_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let unit_re = UNIT_RE.get_or_init(|| Regex::new(r"^\s*unit=([^,;]+)").expect("regex"));
    static PROPERTY_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let property_re =
        PROPERTY_RE.get_or_init(|| Regex::new(r"(?i)^\s*(ty|cm|te)\s*=\s*(.*?);?\s*$").expect("regex"));
    static AR_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let ar_re = AR_RE
        .get_or_init(|| Regex::new(r"(?i)\bar=\(\s*f=([^,]+),\s*t=([^,)]+)").expect("regex"));

    for (line_number, line) in py_splitlines(source_text).into_iter().enumerate() {
        let line_number = line_number as i64 + 1;
        if let Ok(Some(captures)) = unit_re.captures(line) {
            let parent_id = stack.last().map(|&index| units[index].unit_id.clone());
            let name = clean(&captures[1]);
            let unit_id = format!(
                "jp1-unit::{}:{}:{}:{}",
                file_path,
                parent_id.as_deref().unwrap_or("root"),
                name,
                line_number
            );
            units.push(Jp1Unit {
                unit_id,
                name,
                file_path: file_path.to_string(),
                parent_id: parent_id.clone(),
                start_line: line_number,
                end_line: line_number,
                unit_type: String::new(),
                comment: String::new(),
                exec_target: String::new(),
            });
            pending = Some(units.len() - 1);
            if let Some(parent) = parent_id {
                relations.push(Jp1Relation {
                    source_id: parent,
                    source_label: "Jp1Unit".to_string(),
                    target_id: units[units.len() - 1].unit_id.clone(),
                    target_label: "Jp1Unit".to_string(),
                    rel_type: "INCLUDES".to_string(),
                    line: line_number,
                    raw_target: String::new(),
                    resolved: true,
                });
            }
            continue;
        }
        if line.contains('{')
            && let Some(index) = pending.take()
        {
            stack.push(index);
            continue;
        }
        if line.contains('}') {
            if let Some(&index) = stack.last() {
                units[index].end_line = line_number;
                stack.pop();
            }
            continue;
        }
        if stack.is_empty() {
            continue;
        }
        if let Ok(Some(captures)) = property_re.captures(line) {
            let key = captures[1].to_lowercase();
            let value = clean(captures.get(2).map(|m| m.as_str()).unwrap_or(""));
            let current = &mut units[*stack.last().expect("non-empty stack")];
            match key.as_str() {
                "ty" => current.unit_type = value,
                "cm" => current.comment = value,
                _ => current.exec_target = value,
            }
        }
        if let Ok(Some(captures)) = ar_re.captures(line) {
            let source = clean(captures.get(1).map(|m| m.as_str()).unwrap_or(""));
            let target = clean(captures.get(2).map(|m| m.as_str()).unwrap_or(""));
            let parent = stack.last().map(|&index| units[index].unit_id.clone());
            arcs.push((parent, source, target, line_number));
        }
    }

    let mut child_index: BTreeMap<(Option<String>, String), String> = BTreeMap::new();
    for unit in &units {
        child_index.insert((unit.parent_id.clone(), unit.name.clone()), unit.unit_id.clone());
    }
    for (parent_id, source_name, target_name, line_number) in arcs {
        let source_id = child_index.get(&(parent_id.clone(), source_name.clone()));
        let target_id = child_index.get(&(parent_id.clone(), target_name.clone()));
        if let (Some(source_id), Some(target_id)) = (source_id, target_id) {
            relations.push(Jp1Relation {
                source_id: source_id.clone(),
                source_label: "Jp1Unit".to_string(),
                target_id: target_id.clone(),
                target_label: "Jp1Unit".to_string(),
                rel_type: "NEXT".to_string(),
                line: line_number,
                raw_target: String::new(),
                resolved: true,
            });
        } else {
            diagnostics.push(Jp1Diagnostic {
                code: "jp1-arc-unresolved".to_string(),
                message: format!("Unable to resolve arc {source_name} -> {target_name}"),
                file_path: file_path.to_string(),
                line: line_number,
            });
        }
    }

    for unit in &units {
        if unit.exec_target.is_empty() {
            continue;
        }
        let (target_id, resolved) = resolve_exec_target(&unit.exec_target, project_root);
        relations.push(Jp1Relation {
            source_id: unit.unit_id.clone(),
            source_label: "Jp1Unit".to_string(),
            target_id: target_id.clone(),
            target_label: "ShellScript".to_string(),
            rel_type: "CALLS".to_string(),
            line: unit.start_line,
            raw_target: unit.exec_target.clone(),
            resolved,
        });
        if !resolved {
            diagnostics.push(Jp1Diagnostic {
                code: "jp1-exec-target-unresolved".to_string(),
                message: format!("Unable to resolve {}", unit.exec_target),
                file_path: file_path.to_string(),
                line: unit.start_line,
            });
        }
    }

    Jp1File {
        file_path: file_path.to_string(),
        encoding: String::new(),
        units,
        relations,
        diagnostics,
    }
}

/// Row JSON cho writer (`build_graph_rows`).
pub fn unit_row(unit: &Jp1Unit, common: &Map<String, Value>) -> Map<String, Value> {
    let mut row = Map::new();
    row.insert("id".into(), json!(unit.unit_id));
    row.insert("name".into(), json!(unit.name));
    row.insert("file_path".into(), json!(unit.file_path));
    row.insert("type".into(), json!(unit.unit_type));
    row.insert("comment".into(), json!(unit.comment));
    row.insert("exec_target".into(), json!(unit.exec_target));
    row.insert("start_line".into(), json!(unit.start_line));
    row.insert("end_line".into(), json!(unit.end_line));
    for (key, value) in common {
        row.insert(key.clone(), value.clone());
    }
    row
}

pub fn relation_row(relation: &Jp1Relation) -> Map<String, Value> {
    serde_json::json!({
        "source_id": relation.source_id,
        "source_label": relation.source_label,
        "target_id": relation.target_id,
        "target_label": relation.target_label,
        "rel_type": relation.rel_type,
        "properties": {
            "line": relation.line,
            "raw_target": relation.raw_target,
            "resolved": relation.resolved,
        },
    })
    .as_object()
    .cloned()
    .expect("relation row")
}

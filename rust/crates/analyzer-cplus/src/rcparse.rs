//! Port `tools/cplus/rc_parser.py` — tolerant parser cho Windows `.rc`/`.rc2`
//! (Resource/UIControl rows + message-map helpers).

use std::collections::BTreeSet;
use std::path::Path;

use serde_json::{json, Map, Value};

use crate::cscan::decode_rc_bytes;
use crate::position::splitext;

pub type Row = Map<String, Value>;

const BLOCK_RESOURCE_TYPES: [&str; 11] = [
    "ACCELERATORS",
    "AFX_DIALOG_LAYOUT",
    "DESIGNINFO",
    "DIALOG",
    "DIALOGEX",
    "MENU",
    "MENUEX",
    "STRINGTABLE",
    "TEXTINCLUDE",
    "TOOLBAR",
    "VERSIONINFO",
];

const CONTROL_WITH_TEXT: [&str; 13] = [
    "AUTO3STATE",
    "AUTOCHECKBOX",
    "AUTORADIOBUTTON",
    "CHECKBOX",
    "CTEXT",
    "DEFPUSHBUTTON",
    "GROUPBOX",
    "LTEXT",
    "PUSHBOX",
    "PUSHBUTTON",
    "RADIOBUTTON",
    "RTEXT",
    "STATE3",
];

const CONTROL_WITHOUT_TEXT: [&str; 4] = ["COMBOBOX", "EDITTEXT", "LISTBOX", "SCROLLBAR"];

/// `read_rc_text` — (text, encoding, lossy).
pub fn read_rc_text(path: &Path) -> (String, String, bool) {
    match std::fs::read(path) {
        Ok(data) => {
            let decoded = decode_rc_bytes(&data);
            (decoded.text, decoded.encoding, decoded.lossy)
        }
        Err(_) => (String::new(), "utf-8".into(), false),
    }
}

/// `_strip_line_comment` — bỏ `//` ngoài chuỗi (coi `""` là escape).
fn strip_line_comment(line: &str) -> String {
    let chars: Vec<char> = line.chars().collect();
    let mut quoted = false;
    let mut index = 0usize;
    while index < chars.len() {
        let ch = chars[index];
        if ch == '"' {
            if quoted && index + 1 < chars.len() && chars[index + 1] == '"' {
                index += 2;
                continue;
            }
            quoted = !quoted;
        } else if !quoted && ch == '/' && index + 1 < chars.len() && chars[index + 1] == '/' {
            return chars[..index].iter().collect();
        }
        index += 1;
    }
    line.to_string()
}

/// `_mask_strings`.
fn mask_strings(line: &str) -> String {
    let mut chars: Vec<char> = strip_line_comment(line).chars().collect();
    let mut quoted = false;
    let mut index = 0usize;
    while index < chars.len() {
        if chars[index] == '"' {
            if quoted && index + 1 < chars.len() && chars[index + 1] == '"' {
                chars[index] = ' ';
                chars[index + 1] = ' ';
                index += 2;
                continue;
            }
            quoted = !quoted;
            chars[index] = ' ';
        } else if quoted {
            chars[index] = ' ';
        }
        index += 1;
    }
    chars.into_iter().collect()
}

/// `_split_fields` — tách theo dấu phẩy ngoài chuỗi/ngoặc.
fn split_fields(text: &str) -> Vec<String> {
    let mut fields: Vec<String> = Vec::new();
    let mut current = String::new();
    let chars: Vec<char> = text.chars().collect();
    let mut quoted = false;
    let mut depth = 0i64;
    let mut index = 0usize;
    while index < chars.len() {
        let ch = chars[index];
        if ch == '"' {
            current.push(ch);
            if quoted && index + 1 < chars.len() && chars[index + 1] == '"' {
                current.push(chars[index + 1]);
                index += 2;
                continue;
            }
            quoted = !quoted;
        } else if !quoted && (ch == '(' || ch == '[') {
            depth += 1;
            current.push(ch);
        } else if !quoted && (ch == ')' || ch == ']') {
            depth = (depth - 1).max(0);
            current.push(ch);
        } else if !quoted && depth == 0 && ch == ',' {
            fields.push(current.trim().to_string());
            current = String::new();
        } else {
            current.push(ch);
        }
        index += 1;
    }
    if !current.is_empty() || text.ends_with(',') {
        fields.push(current.trim().to_string());
    }
    fields
}

/// `_unquote`.
fn unquote(value: &str) -> String {
    let value = value.trim();
    let value = if value.len() >= 2 && value.starts_with('"') && value.ends_with('"') {
        &value[1..value.len() - 1]
    } else {
        value
    };
    value.replace("\"\"", "\"").replace("\\0", "")
}

/// `_int_or_none` — int(value, 0) hỗ trợ 0x/0o prefix.
fn int_or_none(value: &str) -> Option<i64> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return None;
    }
    let (negative, body) = match trimmed.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, trimmed),
    };
    let parsed = if let Some(hexpart) = body
        .strip_prefix("0x")
        .or_else(|| body.strip_prefix("0X"))
    {
        i64::from_str_radix(hexpart, 16).ok()
    } else if body.len() > 1 && body.starts_with('0') {
        // Python int(x, 0): "011" là/oct invalid → error. "0o17" → octal.
        if let Some(oct) = body.strip_prefix("0o") {
            i64::from_str_radix(oct, 8).ok()
        } else if body.chars().all(|c| c.is_ascii_digit()) && body != "0" {
            // Python int("011", 0) raises ValueError (legacy octal không nhận).
            None
        } else {
            body.parse::<i64>().ok()
        }
    } else {
        body.parse::<i64>().ok()
    };
    parsed.map(|v| if negative { -v } else { v })
}

fn stable_symbol(symbol: &str, fallback: &str) -> String {
    let source = if symbol.is_empty() { fallback } else { symbol };
    let re = regex::Regex::new(r"[^A-Za-z0-9_.:-]+").expect("symbol regex");
    re.replace_all(source, "_").to_string()
}

fn resource_id(rel_path: &str, kind: &str, symbol: &str, line: usize) -> String {
    format!(
        "resource::{}::{}::{}",
        rel_path,
        kind,
        stable_symbol(symbol, &format!("line_{line}"))
    )
}

fn control_id(rel_path: &str, dialog_symbol: &str, symbol: &str, line: usize) -> String {
    format!(
        "ui::{}::{}::{}::{}",
        rel_path,
        dialog_symbol,
        stable_symbol(symbol, "anonymous"),
        line
    )
}

fn condition_text(stack: &[String]) -> String {
    stack
        .iter()
        .filter(|item| !item.is_empty())
        .cloned()
        .collect::<Vec<_>>()
        .join(" && ")
}

fn update_conditions(code: &str, stack: &mut Vec<String>) {
    let if_re = regex::Regex::new(r"^\s*#\s*if\s+(.+)$").expect("if regex");
    let ifdef_re = regex::Regex::new(r"^\s*#\s*ifdef\s+(.+)$").expect("ifdef regex");
    let ifndef_re = regex::Regex::new(r"^\s*#\s*ifndef\s+(.+)$").expect("ifndef regex");
    let else_re = regex::Regex::new(r"^\s*#\s*else\b").expect("else regex");
    let elif_re = regex::Regex::new(r"^\s*#\s*elif\s+(.+)$").expect("elif regex");
    let endif_re = regex::Regex::new(r"^\s*#\s*endif\b").expect("endif regex");
    if let Some(caps) = if_re.captures(code) {
        stack.push(caps[1].trim().to_string());
    } else if let Some(caps) = ifdef_re.captures(code) {
        stack.push(format!("defined({})", caps[1].trim()));
    } else if let Some(caps) = ifndef_re.captures(code) {
        stack.push(format!("!defined({})", caps[1].trim()));
    } else if else_re.is_match(code) {
        if let Some(last) = stack.last_mut() {
            *last = format!("!({last})");
        }
    } else if let Some(caps) = elif_re.captures(code) {
        if let Some(last) = stack.last_mut() {
            *last = caps[1].trim().to_string();
        }
    } else if endif_re.is_match(code) {
        stack.pop();
    }
}

/// `_resource_header` — (symbol, type, tail).
fn resource_header(code: &str, line: usize) -> Option<(String, String, String)> {
    let stripped = code.trim();
    let st_re = regex::Regex::new(r"(?i)^STRINGTABLE\b").expect("stringtable regex");
    if st_re.is_match(stripped) {
        return Some((
            format!("STRINGTABLE@{line}"),
            "STRINGTABLE".to_string(),
            stripped["STRINGTABLE".len()..].trim().to_string(),
        ));
    }
    let gd_re = regex::Regex::new(r"(?i)^GUIDELINES\s+DESIGNINFO\b").expect("guidelines regex");
    if gd_re.is_match(stripped) {
        return Some((format!("DESIGNINFO@{line}"), "DESIGNINFO".to_string(), stripped.to_string()));
    }
    let header_re = regex::Regex::new(
        r"(?i)^([^\s]+)\s+(DIALOGEX|DIALOG|VERSIONINFO|TEXTINCLUDE|AFX_DIALOG_LAYOUT|MENUEX|MENU|ACCELERATORS|TOOLBAR|ICON|BITMAP|CURSOR|RCDATA|HTML|MANIFEST|MESSAGETABLE|AVI|FONT)\b(.*)$",
    )
    .expect("header regex");
    let caps = header_re.captures(stripped)?;
    Some((
        caps[1].to_string(),
        caps[2].to_uppercase(),
        caps[3].trim().to_string(),
    ))
}

/// `_block_end` — index của END khớp BEGIN đầu tiên; không tìm thấy trả header_index.
fn block_end(lines: &[&str], header_index: usize) -> usize {
    let begin_end_re = regex::Regex::new(r"(?i)\b(?:BEGIN|END)\b").expect("begin/end regex");
    let mut depth = 0i64;
    let mut started = false;
    for index in header_index..lines.len() {
        let masked = mask_strings(lines[index]);
        for token in begin_end_re.find_iter(&masked) {
            if token.as_str().to_uppercase() == "BEGIN" {
                depth += 1;
                started = true;
            } else if started {
                depth -= 1;
                if depth == 0 {
                    return index;
                }
            }
        }
        if index > header_index + 20000 {
            break;
        }
    }
    header_index
}

/// `_find_begin`.
fn find_begin(lines: &[&str], start: usize, end: usize) -> Option<usize> {
    let begin_re = regex::Regex::new(r"(?i)\bBEGIN\b").expect("begin regex");
    (start..=end.min(lines.len().saturating_sub(1)))
        .find(|&index| begin_re.is_match(&mask_strings(lines[index])))
}

/// `_quoted_value`.
fn quoted_value(code: &str) -> String {
    let re = regex::Regex::new(r#""(?:[^"]|"")*""#).expect("quoted regex");
    match re.find(code) {
        Some(m) => unquote(m.as_str()),
        None => String::new(),
    }
}

fn int_row_or_null(value: Option<i64>) -> Value {
    match value {
        Some(v) => json!(v),
        None => Value::Null,
    }
}

/// `_parse_control` (các nhánh gán giống nhau là literal port của Python).
#[allow(clippy::too_many_arguments)]
#[allow(clippy::if_same_then_else)]
fn parse_control(
    code: &str,
    rel_path: &str,
    dialog_symbol: &str,
    dialog_id: &str,
    line: usize,
    condition: &str,
) -> Option<Row> {
    let match_re = regex::Regex::new(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s+(.*)$").expect("control regex");
    let caps = match_re.captures(code)?;
    let keyword = caps[1].to_uppercase();
    let fields = split_fields(&caps[2]);
    // Nếu vẫn khởi tạo và không rơi nhánh nào → None (khớp Python).
    if !(keyword == "CONTROL" && fields.len() >= 8)
        && !(keyword == "ICON" && fields.len() >= 6)
        && !(CONTROL_WITH_TEXT.contains(&keyword.as_str()) && fields.len() >= 6)
        && !(CONTROL_WITHOUT_TEXT.contains(&keyword.as_str()) && fields.len() >= 5)
    {
        return None;
    }
    let text = if keyword == "CONTROL" || CONTROL_WITH_TEXT.contains(&keyword.as_str()) {
        unquote(&fields[0])
    } else {
        String::new()
    };
    let resource_ref = if keyword == "ICON" {
        fields[0].clone()
    } else {
        String::new()
    };
    let symbol = if keyword == "CONTROL" {
        fields[1].clone()
    } else if keyword == "ICON" {
        fields[1].clone()
    } else if CONTROL_WITH_TEXT.contains(&keyword.as_str()) {
        fields[1].clone()
    } else {
        fields[0].clone()
    };
    let style = if keyword == "CONTROL" {
        fields
            .iter()
            .take(4)
            .skip(2)
            .filter(|part| !part.is_empty())
            .cloned()
            .collect::<Vec<_>>()
            .join(" | ")
    } else if keyword == "ICON" {
        fields[6..].join(", ")
    } else if CONTROL_WITH_TEXT.contains(&keyword.as_str()) {
        fields[6..].join(", ")
    } else {
        fields[5..].join(", ")
    };
    let coords: Vec<String> = if keyword == "CONTROL" {
        fields[4..8].to_vec()
    } else if keyword == "ICON" {
        fields[2..6].to_vec()
    } else if CONTROL_WITH_TEXT.contains(&keyword.as_str()) {
        fields[2..6].to_vec()
    } else {
        fields[1..5].to_vec()
    };

    let x = int_or_none(&coords[0]);
    let y = int_or_none(coords.get(1).map(String::as_str).unwrap_or(""));
    let width = int_or_none(coords.get(2).map(String::as_str).unwrap_or(""));
    let height = int_or_none(coords.get(3).map(String::as_str).unwrap_or(""));
    let symbol = symbol.trim().to_string();
    let cid = control_id(rel_path, dialog_symbol, &symbol, line);
    let mut note_parts: Vec<String> = vec![format!("{keyword} control"), symbol.clone()];
    if !text.is_empty() {
        note_parts.push(text.clone());
    }
    if !resource_ref.is_empty() {
        note_parts.push(format!("resource {resource_ref}"));
    }
    let mut row = Map::new();
    row.insert("symbol_id".into(), json!(cid));
    row.insert("qualified_name".into(), json!(format!("{dialog_symbol}::{symbol}@{line}")));
    row.insert(
        "name".into(),
        json!(if symbol.is_empty() { format!("{keyword}@{line}") } else { symbol.clone() }),
    );
    row.insert("kind".into(), json!("ui_control"));
    row.insert("control_type".into(), json!(keyword.to_lowercase()));
    row.insert("resource_symbol".into(), json!(symbol));
    row.insert("resource_ref".into(), json!(resource_ref));
    row.insert("dialog_id".into(), json!(dialog_id));
    row.insert("dialog_symbol".into(), json!(dialog_symbol));
    row.insert("text".into(), json!(text));
    row.insert("style".into(), json!(style));
    row.insert("x".into(), int_row_or_null(x));
    row.insert("y".into(), int_row_or_null(y));
    row.insert("width".into(), int_row_or_null(width));
    row.insert("height".into(), int_row_or_null(height));
    row.insert("file_path".into(), json!(rel_path));
    row.insert("start_line".into(), json!(line as i64));
    row.insert("end_line".into(), json!(line as i64));
    row.insert("code".into(), json!(code.trim()));
    row.insert("comment".into(), json!(""));
    row.insert("summary".into(), json!(text));
    row.insert(
        "note".into(),
        json!(note_parts
            .iter()
            .filter(|p| !p.is_empty())
            .cloned()
            .collect::<Vec<_>>()
            .join(" ")),
    );
    row.insert("condition".into(), json!(condition));
    Some(row)
}

fn json_sorted(metadata: &std::collections::BTreeMap<String, Value>) -> String {
    // json.dumps(..., ensure_ascii=False, sort_keys=True) — separators mặc định
    // của Python là (", ", ": ") — KHÔNG compact.
    let mut out = String::from("{");
    for (index, (key, value)) in metadata.iter().enumerate() {
        if index > 0 {
            out.push_str(", ");
        }
        out.push_str(&serde_json::Value::String(key.clone()).to_string());
        out.push_str(": ");
        out.push_str(&dumps_python_spaces(value));
    }
    out.push('}');
    out
}

/// json.dumps với separators mặc định (", ", ": ") cho value metadata.
fn dumps_python_spaces(value: &Value) -> String {
    match value {
        Value::Array(items) => {
            let parts: Vec<String> = items.iter().map(dumps_python_spaces).collect();
            format!("[{}]", parts.join(", "))
        }
        Value::Object(map) => {
            let mut out = String::from("{");
            for (index, (key, item)) in map.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                out.push_str(&serde_json::Value::String(key.clone()).to_string());
                out.push_str(": ");
                out.push_str(&dumps_python_spaces(item));
            }
            out.push('}');
            out
        }
        other => other.to_string(),
    }
}

/// `_parse_dialog` — trả controls; mutate resource (caption/style/note/...).
fn parse_dialog(
    lines: &[&str],
    start: usize,
    end: usize,
    resource: &mut Row,
    condition: &str,
) -> Vec<Row> {
    let Some(begin) = find_begin(lines, start, end) else {
        return Vec::new();
    };
    let mut metadata: std::collections::BTreeMap<String, Value> = std::collections::BTreeMap::new();
    let header_tail = resource
        .remove("_header_tail")
        .map(|v| v.as_str().unwrap_or("").to_string())
        .unwrap_or_default();
    let header_fields = split_fields(&header_tail);
    if header_fields.len() >= 4 {
        metadata.insert(
            "bounds".into(),
            Value::Array(
                header_fields[header_fields.len() - 4..]
                    .iter()
                    .map(|v| int_row_or_null(int_or_none(v)))
                    .collect(),
            ),
        );
    }
    let prop_re = regex::Regex::new(r"(?i)^(CAPTION|STYLE|EXSTYLE|FONT)\s+(.+)$").expect("prop regex");
    for index in start + 1..begin {
        let code = strip_line_comment(lines[index]).trim().to_string();
        if let Some(caps) = prop_re.captures(&code) {
            let key = caps[1].to_lowercase();
            let value = caps[2].trim().to_string();
            if key == "caption" {
                metadata.insert(key, Value::String(quoted_value(&value)));
            } else {
                metadata.insert(key, Value::String(value));
            }
        }
    }
    resource.insert(
        "caption".into(),
        json!(metadata.get("caption").cloned().unwrap_or_default()),
    );
    resource.insert(
        "style".into(),
        json!(metadata.get("style").cloned().unwrap_or_default()),
    );

    let mut controls: Vec<Row> = Vec::new();
    let begin_end_re = regex::Regex::new(r"(?i)\b(?:BEGIN|END)\b").expect("begin/end regex");
    let mut depth = 0i64;
    for index in begin..=end.min(lines.len().saturating_sub(1)) {
        let masked = mask_strings(lines[index]);
        let upper_tokens: Vec<bool> = begin_end_re
            .find_iter(&masked)
            .map(|t| t.as_str().to_uppercase() == "BEGIN")
            .collect();
        if index > begin && depth == 1 {
            let file_path = resource["file_path"].as_str().unwrap_or("").to_string();
            let dialog_symbol = resource["resource_symbol"].as_str().unwrap_or("").to_string();
            let dialog_id = resource["symbol_id"].as_str().unwrap_or("").to_string();
            if let Some(control) = parse_control(
                strip_line_comment(lines[index]).trim(),
                &file_path,
                &dialog_symbol,
                &dialog_id,
                index + 1,
                condition,
            ) {
                controls.push(control);
            }
        }
        for is_begin in upper_tokens {
            depth += if is_begin { 1 } else { -1 };
        }
    }

    metadata.insert("control_count".into(), Value::from(controls.len() as i64));
    resource.insert("metadata_json".into(), json!(json_sorted(&metadata)));
    let control_summary = controls
        .iter()
        .filter(|item| {
            item["resource_symbol"].as_str().unwrap_or("") != "IDC_STATIC"
                || !item["text"].as_str().unwrap_or("").is_empty()
        })
        .map(|item| {
            [
                item["control_type"].as_str().unwrap_or(""),
                item["resource_symbol"].as_str().unwrap_or(""),
                item["text"].as_str().unwrap_or(""),
            ]
            .iter()
            .filter(|p| !p.is_empty())
            .map(|s| s.to_string())
            .collect::<Vec<_>>()
            .join(" ")
        })
        .collect::<Vec<_>>()
        .join("; ");
    let note_parts = [
        format!("Dialog {}", resource["resource_symbol"].as_str().unwrap_or("")),
        resource["caption"].as_str().unwrap_or("").to_string(),
        control_summary,
    ];
    resource.insert(
        "note".into(),
        json!(note_parts
            .iter()
            .filter(|p| !p.is_empty())
            .cloned()
            .collect::<Vec<_>>()
            .join(" | ")),
    );
    let caption = resource["caption"].as_str().unwrap_or("").to_string();
    resource.insert("summary".into(), json!(caption));
    controls
}

/// `_parse_value_metadata`.
fn parse_value_metadata(lines: &[&str], start: usize, end: usize) -> std::collections::BTreeMap<String, Value> {
    let mut values = std::collections::BTreeMap::new();
    let value_re = regex::Regex::new(r"(?i)^VALUE\s+(.+)$").expect("value regex");
    for index in start..=end.min(lines.len().saturating_sub(1)) {
        let code = strip_line_comment(lines[index]).trim().to_string();
        let Some(caps) = value_re.captures(&code) else {
            continue;
        };
        let fields = split_fields(&caps[1]);
        if fields.len() >= 2 {
            values.insert(
                unquote(&fields[0]),
                Value::String(unquote(&fields[1..].join(", "))),
            );
        }
    }
    values
}

/// `_parse_string_table` — (symbol, text, line).
fn parse_string_table(lines: &[&str], start: usize, end: usize) -> Vec<(String, String, usize)> {
    let mut values = Vec::new();
    let entry_re = regex::Regex::new(r#"^([^\s]+)\s+(".*")\s*$"#).expect("entry regex");
    for index in start..=end.min(lines.len().saturating_sub(1)) {
        let code = strip_line_comment(lines[index]).trim().to_string();
        if let Some(caps) = entry_re.captures(&code) {
            values.push((caps[1].to_string(), unquote(&caps[2]), index + 1));
        }
    }
    values
}

fn resource_note(resource: &Row, metadata: &std::collections::BTreeMap<String, Value>) -> String {
    let value_str = |value: &Value| value.as_str().unwrap_or("").to_string();
    let _ = &value_str;

    let mut parts: Vec<String> = vec![
        resource["kind"].as_str().unwrap_or("").to_string(),
        resource["resource_symbol"].as_str().unwrap_or("").to_string(),
    ];
    if let Some(caption) = resource.get("caption").and_then(Value::as_str)
        && !caption.is_empty() {
            parts.push(caption.to_string());
        }
    if let Some(asset) = resource.get("asset_path").and_then(Value::as_str)
        && !asset.is_empty() {
            parts.push(asset.to_string());
        }
    for (key, value) in metadata {
        let rendered = match value {
            Value::String(text) => text.clone(),
            other => other.to_string(),
        };
        if !rendered.is_empty() {
            parts.push(format!("{key}: {rendered}"));
        }
    }
    parts
        .iter()
        .filter(|p| !p.is_empty())
        .cloned()
        .collect::<Vec<_>>()
        .join(" | ")
}

/// `parse_rc_file` — payload dict khớp Python.
pub fn parse_rc_file(path: &Path, root: &Path) -> Row {
    let (raw_text, encoding, lossy) = read_rc_text(path);
    let text = raw_text.replace("\r\n", "\n").replace('\r', "\n");
    let lines: Vec<&str> = text.lines().collect();
    let rel_path = path
        .strip_prefix(root)
        .unwrap_or(path)
        .components()
        .map(|c| c.as_os_str().to_string_lossy().to_string())
        .collect::<Vec<_>>()
        .join("/");
    let mut includes: Vec<String> = Vec::new();
    let mut macros: std::collections::BTreeMap<String, String> = std::collections::BTreeMap::new();
    let mut resources: Vec<Row> = Vec::new();
    let mut elements: Vec<Row> = Vec::new();
    let mut relations: Vec<Row> = Vec::new();
    let mut conditions: Vec<String> = Vec::new();
    let mut language = String::new();
    let mut diagnostics: Vec<String> = Vec::new();
    let mut index = 0usize;

    let cond_re = regex::Regex::new(r"^#\s*(?:if|ifdef|ifndef|else|elif|endif)\b").expect("cond regex");
    let include_re = regex::Regex::new(r#"^#\s*include\s+[<"]([^>"]+)[>"]"#).expect("rc include regex");
    let define_re = regex::Regex::new(r"^#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.+)$").expect("rc define regex");
    let lang_re = regex::Regex::new(r"(?i)^LANGUAGE\s+(.+)$").expect("lang regex");

    while index < lines.len() {
        let raw = lines[index];
        let code = strip_line_comment(raw).trim().to_string();
        if cond_re.is_match(&code) {
            update_conditions(&code, &mut conditions);
            index += 1;
            continue;
        }
        if let Some(caps) = include_re.captures(&code) {
            includes.push(caps[1].to_string());
        }
        if let Some(caps) = define_re.captures(&code) {
            macros.insert(caps[1].to_string(), caps[2].trim().to_string());
        }
        if let Some(caps) = lang_re.captures(&code) {
            language = caps[1].trim().to_string();
        }
        let Some((symbol, resource_type, header_tail)) = resource_header(&code, index + 1) else {
            index += 1;
            continue;
        };
        let kind = resource_type.to_lowercase();
        let end = if BLOCK_RESOURCE_TYPES.contains(&resource_type.as_str()) {
            block_end(&lines, index)
        } else {
            index
        };
        if BLOCK_RESOURCE_TYPES.contains(&resource_type.as_str()) && end == index {
            diagnostics.push(format!("unterminated {resource_type} at line {}", index + 1));
        }
        let snippet = lines[index..=end.min(lines.len() - 1)].join("\n");
        let condition = condition_text(&conditions);
        let mut resource = Map::new();
        resource.insert(
            "symbol_id".into(),
            json!(resource_id(&rel_path, &kind, &symbol, index + 1)),
        );
        resource.insert("qualified_name".into(), json!(format!("{rel_path}::{symbol}")));
        resource.insert("name".into(), json!(symbol));
        resource.insert("kind".into(), json!(kind));
        resource.insert("scope_name".into(), Value::Null);
        resource.insert("resource_symbol".into(), json!(symbol.clone()));
        resource.insert("numeric_id".into(), Value::Null);
        resource.insert("language".into(), json!(language));
        resource.insert("caption".into(), json!(""));
        resource.insert("style".into(), json!(""));
        resource.insert("asset_path".into(), json!(""));
        resource.insert("condition".into(), json!(condition));
        resource.insert("encoding".into(), json!(encoding));
        resource.insert("metadata_json".into(), json!("{}"));
        resource.insert("file_path".into(), json!(rel_path));
        resource.insert("start_byte".into(), json!(0));
        resource.insert("end_byte".into(), json!(0));
        resource.insert("start_line".into(), json!((index + 1) as i64));
        resource.insert("end_line".into(), json!((end + 1) as i64));
        resource.insert("arity".into(), json!(0));
        resource.insert("code".into(), json!(snippet));
        resource.insert("comment".into(), json!(""));
        resource.insert("summary".into(), json!(""));
        resource.insert("note".into(), json!(""));
        resource.insert("_header_tail".into(), json!(header_tail));

        let mut metadata: std::collections::BTreeMap<String, Value> = std::collections::BTreeMap::new();
        let mut child_resources: Vec<Row> = Vec::new();
        if matches!(resource_type.as_str(), "DIALOG" | "DIALOGEX") {
            let controls = parse_dialog(&lines, index, end, &mut resource, &condition);
            elements.extend(controls.iter().cloned());
            for control in &controls {
                relations.push(relation(
                    "Resource",
                    "UIControl",
                    resource["symbol_id"].as_str().unwrap_or(""),
                    control["symbol_id"].as_str().unwrap_or(""),
                ));
            }
        } else if resource_type == "STRINGTABLE" {
            let string_entries = parse_string_table(&lines, index, end);
            for (entry_symbol, entry_text, entry_line) in &string_entries {
                let _ = entry_line;
                metadata.insert(entry_symbol.clone(), Value::String(entry_text.clone()));
            }
            resource.insert("metadata_json".into(), json!(json_sorted(&metadata)));
            resource.insert("note".into(), json!(resource_note(&resource, &metadata)));
            resource.insert(
                "summary".into(),
                json!(metadata
                    .values()
                    .filter_map(|v| v.as_str())
                    .map(str::to_string)
                    .collect::<Vec<_>>()
                    .join("; ")),
            );
            for (entry_symbol, entry_text, entry_line) in &string_entries {
                let mut child = Map::new();
                child.insert(
                    "symbol_id".into(),
                    json!(resource_id(&rel_path, "string", entry_symbol, *entry_line)),
                );
                child.insert(
                    "qualified_name".into(),
                    json!(format!("{rel_path}::{entry_symbol}")),
                );
                child.insert("name".into(), json!(entry_symbol));
                child.insert("kind".into(), json!("string"));
                child.insert("scope_name".into(), Value::Null);
                child.insert("resource_symbol".into(), json!(entry_symbol));
                child.insert("numeric_id".into(), Value::Null);
                child.insert("language".into(), json!(language));
                child.insert("caption".into(), json!(entry_text));
                child.insert("style".into(), json!(""));
                child.insert("asset_path".into(), json!(""));
                child.insert("condition".into(), json!(resource["condition"].clone()));
                child.insert("encoding".into(), json!(encoding));
                child.insert("metadata_json".into(), json!("{}"));
                child.insert("file_path".into(), json!(rel_path));
                child.insert("start_byte".into(), json!(0));
                child.insert("end_byte".into(), json!(0));
                child.insert("start_line".into(), json!(*entry_line as i64));
                child.insert("end_line".into(), json!(*entry_line as i64));
                child.insert("arity".into(), json!(0));
                child.insert(
                    "code".into(),
                    json!(format!("{entry_symbol} \"{entry_text}\"")),
                );
                child.insert("comment".into(), json!(""));
                child.insert("summary".into(), json!(entry_text));
                child.insert(
                    "note".into(),
                    json!(format!("String resource {entry_symbol} | {entry_text}")),
                );
                relations.push(relation(
                    "Resource",
                    "Resource",
                    resource["symbol_id"].as_str().unwrap_or(""),
                    child["symbol_id"].as_str().unwrap_or(""),
                ));
                child_resources.push(child);
            }
        } else if resource_type == "VERSIONINFO" {
            metadata = parse_value_metadata(&lines, index, end);
            resource.insert("metadata_json".into(), json!(json_sorted(&metadata)));
            resource.insert("note".into(), json!(resource_note(&resource, &metadata)));
            resource.insert(
                "summary".into(),
                json!(metadata
                    .get("FileDescription")
                    .or_else(|| metadata.get("ProductName"))
                    .and_then(Value::as_str)
                    .unwrap_or("")),
            );
        } else {
            let tail = resource
                .get("_header_tail")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            resource.insert("asset_path".into(), json!(quoted_value(&tail)));
            resource.insert("metadata_json".into(), json!(json_sorted(&metadata)));
            resource.insert("note".into(), json!(resource_note(&resource, &metadata)));
        }
        resource.remove("_header_tail");
        resources.push(resource);
        resources.extend(child_resources);
        index = end + 1;
    }

    let mut file_comment = String::new();
    for line in lines.iter().take(20) {
        let stripped = line.trim();
        if let Some(rest) = stripped.strip_prefix("//") {
            file_comment = rest.trim().to_string();
            if !file_comment.is_empty() {
                break;
            }
        }
    }
    let file_note = if !resources.is_empty() || !elements.is_empty() {
        format!(
            "Windows resource script with {} resources and {} UI controls",
            resources.len(),
            elements.len()
        )
    } else {
        "Windows resource script with no structured resources".to_string()
    };

    // includes list(dict.fromkeys(...)) — dedupe giữ thứ tự.
    let mut seen = BTreeSet::new();
    let includes: Vec<String> = includes
        .into_iter()
        .filter(|item| seen.insert(item.clone()))
        .collect();

    let mut payload = Map::new();
    payload.insert("functions".into(), json!([]));
    payload.insert("calls".into(), json!([]));
    payload.insert("types".into(), json!([]));
    payload.insert("namespaces".into(), json!([]));
    payload.insert(
        "relations".into(),
        Value::Array(relations.into_iter().map(Value::Object).collect()),
    );
    payload.insert("function_types".into(), json!([]));
    payload.insert("fields".into(), json!([]));
    payload.insert("aliases".into(), json!([]));
    payload.insert("templates".into(), json!([]));
    payload.insert(
        "resources".into(),
        Value::Array(resources.into_iter().map(Value::Object).collect()),
    );
    payload.insert(
        "resource_elements".into(),
        Value::Array(elements.into_iter().map(Value::Object).collect()),
    );
    let mut file_def = Map::new();
    file_def.insert("file_path".into(), json!(rel_path));
    file_def.insert("start_line".into(), json!(1));
    file_def.insert(
        "end_line".into(),
        json!((lines.len() as i64).max(1)),
    );
    file_def.insert("code".into(), json!(text));
    file_def.insert("comment".into(), json!(file_comment.clone()));
    file_def.insert("summary".into(), json!(file_comment));
    file_def.insert("note".into(), json!(file_note));
    payload.insert("file_def".into(), Value::Object(file_def));
    payload.insert("using_namespaces".into(), json!([]));
    payload.insert("using_imports".into(), json!({}));
    payload.insert("includes".into(), json!(includes));
    payload.insert(
        "macros".into(),
        Value::Object(
            macros
                .into_iter()
                .map(|(k, v)| (k, Value::String(v)))
                .collect(),
        ),
    );
    let mut parse_meta = Map::new();
    parse_meta.insert("parser_language".into(), json!("windows_rc"));
    parse_meta.insert("parser_language_initial".into(), json!("windows_rc"));
    parse_meta.insert("encoding".into(), json!(encoding));
    parse_meta.insert("lossy_decode".into(), json!(lossy));
    parse_meta.insert("header_retry_attempted".into(), json!(false));
    parse_meta.insert("header_retry_selected".into(), json!(false));
    parse_meta.insert("has_error".into(), json!(!diagnostics.is_empty()));
    parse_meta.insert("error_nodes".into(), json!(diagnostics.len() as i64));
    parse_meta.insert(
        "error_nodes_initial".into(),
        json!(diagnostics.len() as i64),
    );
    parse_meta.insert("header_retry_error_nodes".into(), Value::Null);
    parse_meta.insert("header_retry_has_error".into(), Value::Null);
    parse_meta.insert(
        "diagnostics".into(),
        Value::Array(diagnostics.into_iter().map(Value::String).collect()),
    );
    payload.insert("parse_meta".into(), Value::Object(parse_meta));
    payload
}

fn relation(
    source_label: &str,
    target_label: &str,
    source_id: &str,
    target_id: &str,
) -> Row {
    let mut row = Map::new();
    row.insert("source_label".into(), json!(source_label));
    row.insert("target_label".into(), json!(target_label));
    row.insert("rel_type".into(), json!("CONTAINS"));
    row.insert("source_id".into(), json!(source_id));
    row.insert("target_id".into(), json!(target_id));
    row.insert("properties".into(), json!({}));
    row
}

/// `extract_resource_tokens` — token identifiers khớp known symbols.
pub fn extract_resource_tokens(text: &str, known_symbols: &BTreeSet<String>) -> Vec<String> {
    if known_symbols.is_empty() {
        return Vec::new();
    }
    let re = regex::Regex::new(r"\b[A-Za-z_][A-Za-z0-9_]*\b").expect("token regex");
    let mut seen = BTreeSet::new();
    re.find_iter(text)
        .filter_map(|m| {
            let token = m.as_str().to_string();
            if known_symbols.contains(&token) && token != "IDC_STATIC" {
                Some(token)
            } else {
                None
            }
        })
        .filter(|token| seen.insert(token.clone()))
        .collect()
}

/// `extract_message_map_handlers`.
pub fn extract_message_map_handlers(
    text: &str,
    known_symbols: &BTreeSet<String>,
) -> Vec<Row> {
    let known = known_symbols;
    let mut results: Vec<Row> = Vec::new();
    let on_re = regex::Regex::new(r"\b(ON_[A-Z0-9_]+)\s*\(([^)]*)\)").expect("ON_ regex");
    let handler_re = regex::Regex::new(r"^(?:[A-Za-z_]\w*::)*[A-Za-z_]\w*$").expect("handler regex");
    let ws_re = regex::Regex::new(r"\s+").expect("ws regex");
    for caps in on_re.captures_iter(text) {
        let macro_name = &caps[1];
        let fields = split_fields(&caps[2]);
        let resource_symbol = fields
            .iter()
            .map(|f| f.trim().to_string())
            .find(|f| known.contains(f))
            .unwrap_or_default();
        if resource_symbol.is_empty() || resource_symbol == "IDC_STATIC" {
            continue;
        }
        let handler = fields
            .last()
            .map(|f| f.trim().trim_start_matches('&').to_string())
            .unwrap_or_default();
        let handler = ws_re.replace_all(&handler, "").to_string();
        if !handler_re.is_match(&handler) {
            continue;
        }
        let line = text[..caps.get(0).unwrap().start()]
            .matches('\n')
            .count()
            + 1;
        let mut row = Map::new();
        row.insert("macro".into(), json!(macro_name));
        row.insert("resource_symbol".into(), json!(resource_symbol));
        row.insert("handler".into(), json!(handler));
        row.insert("line".into(), json!(line as i64));
        results.push(row);
    }
    results
}

/// `is_windows_resource_file`.
pub fn is_windows_resource_file(path: &Path) -> bool {
    matches!(
        splitext(&path.to_string_lossy().to_lowercase()).1.as_str(),
        ".rc" | ".rc2"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn split_fields_basic() {
        assert_eq!(split_fields(r#""a, b", 1, 2"#), vec![r#""a, b""#, "1", "2"]);
    }

    #[test]
    fn int_or_none_prefixes() {
        assert_eq!(int_or_none("10"), Some(10));
        assert_eq!(int_or_none("0x1F"), Some(31));
        assert_eq!(int_or_none("IDC"), None);
    }
}

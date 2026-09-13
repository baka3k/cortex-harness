//! Tree-sitter-assisted, source-evidence-preserving COBOL extraction — port
//! `parser.py`. Toàn bộ regex/char-position/encoding semantics giữ nguyên từng
//! chữ (fixed-format indicator col 7, area A/B, cp037/cp1252 decode, EXEC
//! block gộp dòng, degraded lines do tree-sitter error nodes).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use fancy_regex::{Captures, Regex};
use serde_json::{json, Map, Value};

use crate::models::{Diagnostic, ParsedCopy, ParsedDataItem, ParsedFile, ParsedFileBinding, ParsedParagraph, ParsedStatement, SourceEvidence};
use crate::parser_runtime::LoadedParser;
use crate::pycompat::{decode_cp037, decode_cp1252, py_splitlines, CobolCodec};

pub const COBOL_EXTENSIONS: [&str; 4] = [".cbl", ".cob", ".cpy", ".copy"];
pub const COPYBOOK_EXTENSIONS: [&str; 4] = [".cpy", ".copy", ".cbl", ".cob"];

const NAME: &str = "[A-Z0-9][A-Z0-9-]*";

/// Python `re.compile(..., re.IGNORECASE)` — fancy-regex case-insensitive.
fn py_regex(pattern: &str) -> Regex {
    Regex::new(&format!("(?i){pattern}")).expect("valid cobol regex")
}

pub struct Patterns {
    division: Regex,
    section: Regex,
    paragraph: Regex,
    data: Regex,
    program: Regex,
    copy: Regex,
    select: Regex,
    fd: Regex,
    exec_block: Regex,
    exec_sql_op: Regex,
    sql_tables: Regex,
    sql_hosts: Regex,
    exec_cics_op: Regex,
    cics_resources: Regex,
    call: Regex,
    perform: Regex,
    goto: Regex,
    alter: Regex,
    open_statement: Regex,
    io: Regex,
    exit: Regex,
    conditional: Regex,
    name_words: Regex,
    pic: Regex,
    usage: Regex,
    value: Regex,
    redefines: Regex,
    occurs: Regex,
    source_free: Regex,
    source_fixed: Regex,
    dialect_cbl: Regex,
}

impl Patterns {
    pub fn new() -> Self {
        Self {
            division: py_regex(&format!(r"^\s*({NAME})\s+DIVISION\s*\.")),
            section: py_regex(&format!(r"^\s*({NAME}(?:-{NAME})*)\s+SECTION\s*\.")),
            paragraph: py_regex(&format!(r"^\s*({NAME})\s*\.\s*(?:\*>.*)?$")),
            data: py_regex(&format!(r"^\s*(\d{{1,2}})\s+({NAME})(.*?)(?:\.\s*)?$")),
            program: py_regex(&format!(r"\bPROGRAM-ID\s*\.\s*({NAME})")),
            copy: py_regex(&format!(
                r#"\bCOPY\s+['"]?({NAME})['"]?(?:\s+(?:OF|IN)\s+{NAME})?(.*?)(?:\.\s*)?$"#
            )),
            select: py_regex(&format!(
                r"\bSELECT\s+(?:OPTIONAL\s+)?({NAME}).*?\bASSIGN(?:\s+TO)?\s+(.+?)(?:\.\s*)?$"
            )),
            fd: py_regex(&format!(r"^\s*(?:FD|SD)\s+({NAME})")),
            exec_block: py_regex(r"\bEXEC\s+(?:SQL|CICS)\b"),
            exec_sql_op: py_regex(r"EXEC\s+SQL\s+([A-Z]+)"),
            sql_tables: py_regex(r"\b(?:FROM|INTO|UPDATE|JOIN|TABLE)\s+([A-Z0-9_.-]+)"),
            sql_hosts: py_regex(r":([A-Z0-9-]+)"),
            exec_cics_op: py_regex(r"EXEC\s+CICS\s+([A-Z]+)"),
            cics_resources: py_regex(
                r#"\b(?:PROGRAM|FILE|DATASET|QUEUE|TRANSID)\s*\(?\s*['"]?([A-Z0-9_.-]+)"#,
            ),
            call: py_regex(&format!(
                r#"\bCALL\s+((?:['"][^'"]+['"])|(?:{NAME}))"#
            )),
            perform: py_regex(&format!(
                r"\bPERFORM\s+({NAME})(?:\s+(?:THRU|THROUGH)\s+({NAME}))?"
            )),
            goto: py_regex(r"\bGO\s+TO\s+(.+)"),
            alter: py_regex(&format!(
                r"\bALTER\s+({NAME})\s+TO\s+PROCEED\s+TO\s+({NAME})"
            )),
            open_statement: py_regex(&format!(
                r"^\s*OPEN\s+(INPUT|OUTPUT|I-O|EXTEND)\s+({NAME})"
            )),
            io: py_regex(&format!(r"^\s*(CLOSE|READ|WRITE|REWRITE|DELETE|START)\s+({NAME})")),
            exit: py_regex(r"^\s*(?:EXIT(?:\s+PROGRAM)?|STOP\s+RUN|GOBACK)\b"),
            conditional: py_regex(r"^\s*(?:IF|ELSE|EVALUATE|WHEN)\b"),
            name_words: py_regex(NAME),
            pic: py_regex(r"\bPIC(?:TURE)?\s+([^\s.]+(?:\([^)]*\))?)"),
            usage: py_regex(r"\bUSAGE(?:\s+IS)?\s+([A-Z0-9-]+)"),
            value: py_regex(r"\bVALUE(?:\s+IS)?\s+(.+?)(?=\s+(?:REDEFINES|OCCURS|USAGE|PIC)\b|\.$|$)"),
            redefines: py_regex(&format!(r"\bREDEFINES\s+({NAME})")),
            occurs: py_regex(r"\bOCCURS\s+(.+?)(?=\s+(?:PIC|USAGE|VALUE|REDEFINES)\b|\.$|$)"),
            source_free: py_regex(r">>SOURCE\s+FORMAT\s+(?:IS\s+)?FREE"),
            source_fixed: py_regex(r">>SOURCE\s+FORMAT\s+(?:IS\s+)?FIXED"),
            // Python re.MULTILINE — ^ tại mỗi line start (ví dụ paragraph tên
            // PROCESS-PARA khớp CBL|PROCESS ⇒ dialect ibm-enterprise).
            dialect_cbl: py_regex(r"(?m)^\s*(?:\d{6}\s+)?(?:CBL|PROCESS)\b"),
        }
    }
}

impl Default for Patterns {
    fn default() -> Self {
        Self::new()
    }
}

fn group<'t>(captures: &'t Captures<'t>, index: usize) -> &'t str {
    captures.get(index).map(|m| m.as_str()).unwrap_or("")
}

/// Python `re.findall` với pattern CÓ group 1 → group 1; KHÔNG group → whole
/// match (vd `re.findall(_NAME, upper)`).
fn findall_first<'t>(regex: &Regex, text: &'t str) -> Vec<&'t str> {
    let mut out = Vec::new();
    for captures in regex.captures_iter(text).flatten() {
        match captures.get(1) {
            Some(m) => out.push(m.as_str()),
            None => {
                if let Some(m) = captures.get(0) {
                    out.push(m.as_str());
                }
            }
        }
    }
    out
}

/// `iter_cobol_files` — root.rglob("*") tương đương: walk đệ quy (KHÔNG theo
/// symlink dir như pathlib ***/rglob), lọc suffix + ẩn phần ".". Sort theo
/// tuple parts như `sorted(paths)` của pathlib.
pub fn iter_cobol_files(root: &Path, extensions: &[String]) -> Vec<PathBuf> {
    let allowed: Vec<String> = extensions
        .iter()
        .map(|value| {
            if let Some(stripped) = value.strip_prefix('.') {
                format!(".{}", stripped.to_lowercase())
            } else {
                format!(".{value}")
            }
            .to_lowercase()
        })
        .collect();
    let mut files: Vec<PathBuf> = Vec::new();
    walk_files(root, root, &allowed, &mut files);
    files.sort_by(|a, b| compare_path_parts(a, b));
    files
}

fn walk_files(root: &Path, dir: &Path, allowed: &[String], files: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let mut subdirs: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(file_type) = entry.file_type() else {
            continue;
        };
        if file_type.is_dir() {
            subdirs.push(path);
            continue;
        }
        if !file_type.is_file() {
            continue;
        }
        let suffix = path
            .extension()
            .map(|ext| format!(".{}", ext.to_string_lossy().to_lowercase()))
            .unwrap_or_default();
        if !allowed.contains(&suffix) {
            continue;
        }
        if hidden_part(&rel_parts(root, &path)) {
            continue;
        }
        files.push(path);
    }
    for sub in subdirs {
        if hidden_part(&rel_parts(root, &sub)) {
            continue;
        }
        walk_files(root, &sub, allowed, files);
    }
}

fn rel_parts(root: &Path, path: &Path) -> Vec<String> {
    path.strip_prefix(root)
        .unwrap_or(path)
        .components()
        .map(|c| c.as_os_str().to_string_lossy().to_string())
        .collect()
}

fn hidden_part(parts: &[String]) -> bool {
    parts.iter().any(|part| part.starts_with('.'))
}

/// pathlib sort — so sánh tuple các phần tử path (không phải full string).
pub fn compare_path_parts(a: &Path, b: &Path) -> std::cmp::Ordering {
    let key = |path: &Path| -> Vec<String> {
        path.components()
            .map(|c| c.as_os_str().to_string_lossy().to_string())
            .collect()
    };
    key(a).cmp(&key(b))
}

pub struct DecodedSource {
    pub text: String,
    pub encoding: String,
    pub diagnostics: Vec<Diagnostic>,
}

/// `_decode_source` — utf-8-sig trước, rồi so điểm EBCDIC cp037 vs cp1252,
/// cuối cùng cp1252 (diagnostic info). cp1252 decode lỗi ⇒ propagate
/// (Python UnicodeDecodeError ⊂ ValueError ⇒ rc=2 staged analysis failed).
pub fn decode_source(data: &[u8]) -> Result<DecodedSource, String> {
    if let Ok(text) = std::str::from_utf8(data) {
        let text = text.strip_prefix('\u{feff}').unwrap_or(text).to_string();
        return Ok(DecodedSource {
            text,
            encoding: "utf-8-sig".to_string(),
            diagnostics: Vec::new(),
        });
    }
    let western = decode_cp1252(data).map_err(|_decode_error| {
        "'cp1252' codec can't decode byte in position: invalid continuation byte".to_string()
    })?;
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    if let Ok(ebcdic) = decode_cp037(data) {
        let keywords = ["DIVISION", "PROGRAM-ID", "PROCEDURE", "WORKING-STORAGE", "COPY", " PIC "];
        let western_score = keywords
            .iter()
            .filter(|keyword| western.to_uppercase().contains(*keyword))
            .count();
        let ebcdic_score = keywords
            .iter()
            .filter(|keyword| ebcdic.to_uppercase().contains(*keyword))
            .count();
        if ebcdic_score > western_score && ebcdic_score > 0 {
            diagnostics.push(Diagnostic::new(
                "COBOL_ENCODING_EBCDIC",
                "decoded source as EBCDIC cp037",
                "info",
            ));
            return Ok(DecodedSource {
                text: ebcdic,
                encoding: "cp037".to_string(),
                diagnostics,
            });
        }
    }
    diagnostics.push(Diagnostic::new(
        "COBOL_ENCODING_WESTERN",
        "decoded non-UTF-8 source as Windows-1252",
        "info",
    ));
    Ok(DecodedSource {
        text: western,
        encoding: "cp1252".to_string(),
        diagnostics,
    })
}

/// `detect_source_format`.
pub fn detect_source_format(patterns: &Patterns, lines: &[&str]) -> String {
    let directive = lines.iter().take(20).cloned().collect::<Vec<_>>().join("\n").to_uppercase();
    if patterns.source_free.is_match(&directive).unwrap_or(false) {
        return "free".to_string();
    }
    if patterns.source_fixed.is_match(&directive).unwrap_or(false) {
        return "fixed".to_string();
    }
    let candidates: Vec<&str> = lines.iter().copied().filter(|line| !line.trim().is_empty()).take(40).collect();
    let fixed = candidates
        .iter()
        .filter(|line| {
            (line.chars().count() >= 7 && py_isdigit(py_char_slice(line, 6).trim()))
                || (line.chars().count() >= 7 && py_isspace_all(py_char_slice(line, 7)))
        })
        .count();
    if !candidates.is_empty() && fixed >= std::cmp::max(2, candidates.len() / 2) {
        "fixed".to_string()
    } else {
        "free".to_string()
    }
}

/// Python `str.isdigit()` — Nd + superscripts ² ³ ¹.
fn py_isdigit(text: &str) -> bool {
    !text.is_empty()
        && text.chars().all(|c| {
            c.is_ascii_digit() || matches!(c, '\u{00b2}' | '\u{00b3}' | '\u{00b9}') || is_nd(c)
        })
}

/// Python str.isdigit cho Unicode — lớp \d của regex crate (chính là Nd) cộng
/// superscript ² ³ ¹ (No nhưng digit=True). Static regex để tránh recompile.
fn is_nd(c: char) -> bool {
    use std::sync::OnceLock;
    static ND: OnceLock<regex::Regex> = OnceLock::new();
    let re = ND.get_or_init(|| regex::Regex::new(r"\d").expect("digit regex"));
    re.is_match(c.to_string().as_str())
}

/// Python `str.isspace()` — White_Space + \x1c-\x1f.
fn py_isspace_all(text: &str) -> bool {
    !text.is_empty()
        && text
            .chars()
            .all(|c| c.is_whitespace() || matches!(c, '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{1f}'))
}

/// `detect_dialect`.
pub fn detect_dialect(patterns: &Patterns, text: &str) -> String {
    let upper = text.to_uppercase();
    if upper.contains("MICRO FOCUS") || upper.contains("$SET") {
        return "micro-focus".to_string();
    }
    if upper.contains("GNUCOBOL") || upper.contains(">>SOURCE") {
        return "gnucobol".to_string();
    }
    if patterns.dialect_cbl.is_match(&upper).unwrap_or(false) || upper.contains("EXEC CICS") {
        return "ibm-enterprise".to_string();
    }
    "ansi".to_string()
}

/// `_line_metrics` — byte offsets/lengths theo codec nguồn.
pub fn line_metrics(text: &str, encoding: &str, data: &[u8]) -> (Vec<i64>, Vec<i64>) {
    let (lines, chunks) = py_splitlines(text);
    let codec = CobolCodec::from_encoding(encoding);
    let mut current: i64 = if encoding == "utf-8-sig" && data.starts_with(&[0xef, 0xbb, 0xbf]) {
        3
    } else {
        0
    };
    let mut offsets: Vec<i64> = Vec::new();
    let mut lengths: Vec<i64> = Vec::new();
    for (index, line) in lines.iter().enumerate() {
        offsets.push(current);
        lengths.push(codec.encode_len(line) as i64);
        let chunk = chunks.get(index).copied().unwrap_or(line);
        current += codec.encode_len(chunk) as i64;
    }
    (offsets, lengths)
}

/// Python char-slice `line[:n]` — an toàn trên biên multi-byte.
fn py_char_slice(line: &str, n: usize) -> &str {
    match line.char_indices().nth(n) {
        Some((index, _)) => &line[..index],
        None => line,
    }
}

/// `_code_text` — fixed format: indicator col 7 (`*`/`/` là comment), area B
/// từ col 8.
pub fn code_text<'a>(line: &'a str, source_format: &str) -> &'a str {
    if source_format != "fixed" || line.chars().count() < 7 {
        return line;
    }
    let indicator = line.chars().nth(6).unwrap_or(' ');
    if indicator == '*' || indicator == '/' {
        return "";
    }
    py_char_drop(line, 7)
}

/// Python `line[n:]` theo char.
fn py_char_drop(line: &str, n: usize) -> &str {
    match line.char_indices().nth(n) {
        Some((index, _)) => &line[index..],
        None => "",
    }
}

/// `_evidence`.
pub fn evidence_at(
    path: &str,
    lines: &[&str],
    offsets: &[i64],
    byte_lengths: &[i64],
    start: usize,
    end: Option<usize>,
) -> SourceEvidence {
    let end_index = end.unwrap_or(start);
    let end_text = lines.get(end_index).copied().unwrap_or("");
    let start_byte = offsets.get(start).copied().unwrap_or(0);
    let end_byte = offsets
        .get(end_index)
        .zip(byte_lengths.get(end_index))
        .map(|(offset, length)| offset + length)
        .unwrap_or(0);
    SourceEvidence::full(
        path,
        start as i64 + 1,
        1,
        end_index as i64 + 1,
        end_text.chars().count() as i64 + 1,
        start_byte,
        end_byte,
    )
}

fn utf8_ignore_decode(bytes: &[u8]) -> String {
    // Python bytes.decode("utf-8", errors="ignore") — bỏ byte invalid.
    let mut out = String::new();
    let mut rest = bytes;
    while !rest.is_empty() {
        match std::str::from_utf8(rest) {
            Ok(text) => {
                out.push_str(text);
                break;
            }
            Err(error) => {
                let valid = error.valid_up_to();
                out.push_str(std::str::from_utf8(&rest[..valid]).unwrap_or_default());
                let skip = error.error_len().unwrap_or(rest.len() - valid).max(1);
                rest = &rest[valid + skip..];
            }
        }
    }
    out
}

pub struct TreeDiagnostics {
    pub diagnostics: Vec<Diagnostic>,
    pub error_count: i64,
}

/// `_tree_diagnostics` — parse UTF-8, walk DFS preorder, đếm error/missing
/// node + map byte offsets ngược về codec nguồn.
pub fn tree_diagnostics(
    parser: &mut LoadedParser,
    text: &str,
    path: &str,
    encoding: &str,
    offsets: &[i64],
    source_length: usize,
) -> TreeDiagnostics {
    let tree = parser.parse_utf8(text.as_bytes());
    let lines: Vec<&str> = py_splitlines(text).0;
    let codec = CobolCodec::from_encoding(encoding);

    let original_byte = |row: usize, utf8_column: usize| -> i64 {
        if row >= lines.len() || row >= offsets.len() {
            return source_length as i64;
        }
        let raw = lines[row].as_bytes();
        let prefix_bytes: &[u8] = if utf8_column <= raw.len() {
            &raw[..utf8_column]
        } else {
            raw
        };
        let prefix = utf8_ignore_decode(prefix_bytes);
        offsets[row] + codec.encode_len(&prefix) as i64
    };

    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let mut error_count = 0i64;
    let Some(tree) = tree else {
        return TreeDiagnostics { diagnostics, error_count };
    };
    let mut stack: Vec<tree_sitter::Node<'_>> = vec![tree.root_node()];
    while let Some(node) = stack.pop() {
        if node.is_error() || node.is_missing() {
            error_count += 1;
            let code = if node.is_missing() {
                "COBOL_SYNTAX_MISSING"
            } else {
                "COBOL_SYNTAX_ERROR"
            };
            let start_point = node.start_position();
            let end_point = node.end_position();
            diagnostics.push(
                Diagnostic::new(
                    code,
                    format!("Tree-sitter reported {} while parsing source", node.kind()),
                    "warning",
                )
                .with_evidence(SourceEvidence::full(
                    path,
                    start_point.row as i64 + 1,
                    start_point.column as i64 + 1,
                    end_point.row as i64 + 1,
                    end_point.column as i64 + 1,
                    original_byte(start_point.row, start_point.column),
                    original_byte(end_point.row, end_point.column),
                ))
                .with_details(json!({"node_type": node.kind()})),
            );
        }
        let named_child_count = node.named_child_count();
        let mut children: Vec<tree_sitter::Node<'_>> = Vec::with_capacity(named_child_count);
        for index in 0..named_child_count {
            if let Some(child) = node.named_child(index) {
                children.push(child);
            }
        }
        children.reverse();
        stack.extend(children);
    }
    TreeDiagnostics {
        diagnostics,
        error_count,
    }
}

fn py_upper_joined(text: &str) -> String {
    text.to_uppercase().split_whitespace().collect::<Vec<_>>().join(" ")
}

/// `_statement` — phân loại 1 statement procedure thành ParsedStatement.
pub fn statement(
    patterns: &Patterns,
    text: &str,
    evidence: SourceEvidence,
    degraded: bool,
) -> Option<ParsedStatement> {
    let upper = py_upper_joined(text);
    let confidence = if degraded { 0.65 } else { 1.0 };
    if upper.is_empty() || upper.starts_with('*') {
        return None;
    }
    if upper.starts_with("EXEC SQL") {
        let operation = patterns
            .exec_sql_op
            .captures(&upper)
            .ok()
            .flatten()
            .map(|c| group(&c, 1).to_string())
            .unwrap_or_default();
        let targets = findall_first(&patterns.sql_tables, &upper)
            .into_iter()
            .map(str::to_string)
            .collect::<Vec<_>>();
        let hosts = findall_first(&patterns.sql_hosts, &upper)
            .into_iter()
            .map(str::to_string)
            .collect::<Vec<_>>();
        return Some(ParsedStatement {
            kind: "sql".to_string(),
            text: text.trim().to_string(),
            evidence,
            properties: json!({
                "operation": operation,
                "targets": targets,
                "host_variables": hosts,
            }),
            confidence,
        });
    }
    if upper.starts_with("EXEC CICS") {
        let operation = patterns
            .exec_cics_op
            .captures(&upper)
            .ok()
            .flatten()
            .map(|c| group(&c, 1).to_string())
            .unwrap_or_default();
        let resources = findall_first(&patterns.cics_resources, &upper)
            .into_iter()
            .map(str::to_string)
            .collect::<Vec<_>>();
        return Some(ParsedStatement {
            kind: "cics".to_string(),
            text: text.trim().to_string(),
            evidence,
            properties: json!({
                "operation": operation,
                "resources": resources,
            }),
            confidence,
        });
    }
    if let Ok(Some(captures)) = patterns.call.captures(&upper) {
        let raw = group(&captures, 1);
        let literal = raw.starts_with('\'') || raw.starts_with('"');
        let conf = if literal { confidence } else { confidence.min(0.6) };
        return Some(ParsedStatement {
            kind: "call".to_string(),
            text: text.trim().to_string(),
            evidence,
            properties: json!({
                "target": raw.trim_matches(|c| c == '\'' || c == '"'),
                "literal": literal,
            }),
            confidence: conf,
        });
    }
    if let Ok(Some(captures)) = patterns.perform.captures(&upper) {
        let target = group(&captures, 1);
        if !matches!(target, "UNTIL" | "VARYING" | "WITH" | "TEST") {
            let through = captures.get(2).map(|m| m.as_str()).unwrap_or("");
            let mut properties = Map::new();
            properties.insert("target".into(), json!(target));
            properties.insert("through".into(), json!(through));
            let padded = format!(" {upper} ");
            if padded.contains(" UNTIL ") {
                properties.insert("loop".into(), json!("until"));
            }
            if padded.contains(" VARYING ") {
                properties.insert("loop".into(), json!("varying"));
            }
            return Some(ParsedStatement {
                kind: "perform".to_string(),
                text: text.trim().to_string(),
                evidence,
                properties: Value::Object(properties),
                confidence,
            });
        }
    }
    if let Ok(Some(captures)) = patterns.goto.captures(&upper) {
        let body = group(&captures, 1).trim_end_matches('.').to_string();
        let (target_text, selector) = match body.split_once(" DEPENDING ON ") {
            Some((target_text, selector)) => (target_text.to_string(), selector.to_string()),
            None => (body.clone(), String::new()),
        };
        let targets = findall_first(&patterns.name_words, &target_text)
            .into_iter()
            .map(str::to_string)
            .collect::<Vec<_>>();
        let conf = if !selector.is_empty() { confidence.min(0.7) } else { confidence };
        return Some(ParsedStatement {
            kind: "goto".to_string(),
            text: text.trim().to_string(),
            evidence,
            properties: json!({
                "targets": targets,
                "selector": selector.trim(),
                "dynamic": !selector.is_empty(),
            }),
            confidence: conf,
        });
    }
    if let Ok(Some(captures)) = patterns.alter.captures(&upper) {
        return Some(ParsedStatement {
            kind: "alter".to_string(),
            text: text.trim().to_string(),
            evidence,
            properties: json!({
                "source": group(&captures, 1),
                "target": group(&captures, 2),
            }),
            confidence: confidence.min(0.5),
        });
    }
    if let Ok(Some(captures)) = patterns.open_statement.captures(&upper) {
        return Some(ParsedStatement {
            kind: "io".to_string(),
            text: text.trim().to_string(),
            evidence,
            properties: json!({
                "operation": "OPEN",
                "mode": group(&captures, 1),
                "target": group(&captures, 2),
            }),
            confidence,
        });
    }
    if let Ok(Some(captures)) = patterns.io.captures(&upper) {
        return Some(ParsedStatement {
            kind: "io".to_string(),
            text: text.trim().to_string(),
            evidence,
            properties: json!({
                "operation": group(&captures, 1),
                "target": group(&captures, 2),
            }),
            confidence,
        });
    }
    if patterns.exit.is_match(&upper).unwrap_or(false) {
        let terminal = upper.contains("STOP RUN") || upper.contains("GOBACK") || upper.contains("EXIT PROGRAM");
        return Some(ParsedStatement {
            kind: "exit".to_string(),
            text: text.trim().to_string(),
            evidence,
            properties: json!({ "terminal": terminal }),
            confidence,
        });
    }
    if patterns.conditional.is_match(&upper).unwrap_or(false) {
        return Some(ParsedStatement {
            kind: "conditional".to_string(),
            text: text.trim().to_string(),
            evidence,
            properties: json!({}),
            confidence,
        });
    }
    let words = findall_first(&patterns.name_words, &upper)
        .into_iter()
        .map(str::to_string)
        .collect::<Vec<_>>();
    if !words.is_empty() {
        return Some(ParsedStatement {
            kind: "statement".to_string(),
            text: text.trim().to_string(),
            evidence,
            properties: json!({ "words": words }),
            confidence,
        });
    }
    None
}

const NON_PARAGRAPH_LABELS: [&str; 11] = [
    "ELSE", "END-IF", "END-EVALUATE", "END-PERFORM", "END-EXEC", "EXIT", "GOBACK", "CONTINUE",
    "NEXT", "RETURN", "STOP",
];

/// `parse_file`.
pub fn parse_file(
    patterns: &Patterns,
    path: &Path,
    root: &Path,
    parser: &mut LoadedParser,
) -> Result<ParsedFile, String> {
    // Python: path.resolve().relative_to(root.resolve()).as_posix().
    let root_canon = std::fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf());
    let path_canon = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    let relative = match path_canon.strip_prefix(&root_canon) {
        Ok(rel) => rel
            .components()
            .map(|c| c.as_os_str().to_string_lossy().to_string())
            .collect::<Vec<_>>()
            .join("/"),
        Err(_) => format!(
            "@copybook/{}/{}",
            path.parent().and_then(|p| p.file_name()).map(|n| n.to_string_lossy().to_string()).unwrap_or_default(),
            path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default()
        ),
    };
    let data = std::fs::read(path).unwrap_or_default();
    let decoded = decode_source(&data)?;
    let text = decoded.text.clone();
    let encoding = decoded.encoding.clone();
    let mut diagnostics = decoded.diagnostics.clone();
    let (lines, _keepends) = py_splitlines(&text);
    let (offsets, byte_lengths) = line_metrics(&text, &encoding, &data);
    let tree = tree_diagnostics(
        parser,
        &text,
        &relative,
        &encoding,
        &offsets,
        data.len(),
    );
    let mut error_count = tree.error_count;
    let tree_diags = tree.diagnostics.clone();
    diagnostics.extend(tree.diagnostics);
    if text.matches('(').count() != text.matches(')').count() {
        let broken_line = lines
            .iter()
            .enumerate()
            .find(|(_, value)| value.matches('(').count() != value.matches(')').count())
            .map(|(index, _)| index)
            .unwrap_or(0);
        diagnostics.push(
            Diagnostic::new(
                "COBOL_SYNTAX_UNBALANCED_DELIMITER",
                "source contains unbalanced parentheses",
                "warning",
            )
            .with_evidence(evidence_at(&relative, &lines, &offsets, &byte_lengths, broken_line, None)),
        );
        error_count += 1;
    }
    let source_format = detect_source_format(patterns, &lines);
    let dialect = detect_dialect(patterns, &text);
    let program_name = patterns
        .program
        .captures(&text)
        .ok()
        .flatten()
        .map(|captures| group(&captures, 1).to_uppercase())
        .unwrap_or_default();
    let is_copybook = matches!(path.extension().map(|e| e.to_string_lossy().to_lowercase()).as_deref(), Some("cpy" | "copy"))
        || program_name.is_empty();

    let mut divisions: Vec<String> = Vec::new();
    let mut sections: Vec<String> = Vec::new();
    let mut data_items: Vec<ParsedDataItem> = Vec::new();
    let mut copies: Vec<ParsedCopy> = Vec::new();
    let mut selects: BTreeMap<String, ParsedFileBinding> = BTreeMap::new();
    let mut described: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    let mut paragraphs: Vec<ParsedParagraph> = Vec::new();
    let mut current_division = String::new();
    let mut current_storage = String::new();
    let mut current_section = String::new();
    let mut current_paragraph: Option<usize> = None;
    let mut degraded_lines: std::collections::BTreeSet<i64> = std::collections::BTreeSet::new();
    for diagnostic in &tree_diags {
        if let Some(evidence) = &diagnostic.evidence {
            for line in evidence.start_line - 1..evidence.end_line {
                degraded_lines.insert(line);
            }
        }
    }

    let mut index = 0usize;
    while index < lines.len() {
        let raw = lines[index];
        let code = code_text(raw, &source_format);
        let stripped = code.trim();
        let upper = stripped.to_uppercase();
        if stripped.is_empty() || upper.starts_with("*>") {
            index += 1;
            continue;
        }
        if let Ok(Some(captures)) = patterns.division.captures(code) {
            current_division = group(&captures, 1).to_uppercase();
            divisions.push(current_division.clone());
            current_storage = String::new();
            current_section = String::new();
            index += 1;
            continue;
        }
        if let Ok(Some(captures)) = patterns.section.captures(code) {
            let name = group(&captures, 1).to_uppercase();
            sections.push(name.clone());
            if current_division == "PROCEDURE" {
                current_section = name;
            } else if current_division == "DATA" {
                current_storage = name;
            }
            index += 1;
            continue;
        }
        if let Ok(Some(captures)) = patterns.copy.captures(code) {
            let tail = group(&captures, 2).trim().to_string();
            let replacing = if tail.to_uppercase().starts_with("REPLACING") { tail } else { String::new() };
            copies.push(ParsedCopy {
                name: group(&captures, 1).to_uppercase(),
                evidence: evidence_at(&relative, &lines, &offsets, &byte_lengths, index, None),
                replacing,
            });
        }
        if let Ok(Some(captures)) = patterns.select.captures(code) {
            let name = group(&captures, 1).to_uppercase();
            selects.insert(
                name.clone(),
                ParsedFileBinding {
                    name,
                    evidence: evidence_at(&relative, &lines, &offsets, &byte_lengths, index, None),
                    assignment: group(&captures, 2)
                        .trim()
                        .trim_matches(|c| c == '\'' || c == '"')
                        .to_string(),
                    has_description: false,
                },
            );
        }
        if let Ok(Some(captures)) = patterns.fd.captures(code) {
            described.insert(group(&captures, 1).to_uppercase());
        }
        if (current_division == "DATA" || is_copybook)
            && let Ok(Some(captures)) = patterns.data.captures(code)
        {
                let level: i64 = group(&captures, 1).parse().unwrap_or(0);
                let name = group(&captures, 2).to_uppercase();
                let tail = group(&captures, 3).to_string();
                let pic = patterns
                    .pic
                    .captures(&tail)
                    .ok()
                    .flatten()
                    .map(|c| group(&c, 1).to_uppercase())
                    .unwrap_or_default();
                let usage = patterns
                    .usage
                    .captures(&tail)
                    .ok()
                    .flatten()
                    .map(|c| group(&c, 1).to_uppercase())
                    .unwrap_or_default();
                let value = patterns
                    .value
                    .captures(&tail)
                    .ok()
                    .flatten()
                    .map(|c| group(&c, 1).trim().to_string())
                    .unwrap_or_default();
                let redefines = patterns
                    .redefines
                    .captures(&tail)
                    .ok()
                    .flatten()
                    .map(|c| group(&c, 1).to_uppercase())
                    .unwrap_or_default();
                let occurs = patterns
                    .occurs
                    .captures(&tail)
                    .ok()
                    .flatten()
                    .map(|c| group(&c, 1).trim().to_string())
                    .unwrap_or_default();
                data_items.push(ParsedDataItem {
                    name,
                    level,
                    storage: if current_storage.is_empty() {
                        if is_copybook { "COPYBOOK".to_string() } else { "DATA".to_string() }
                    } else {
                        current_storage.clone()
                    },
                    evidence: evidence_at(&relative, &lines, &offsets, &byte_lengths, index, None),
                    picture: pic,
                    usage,
                    value,
                    redefines,
                    occurs,
                });
        }
        if current_division == "PROCEDURE" {
            let paragraph_match = patterns.paragraph.captures(code).ok().flatten();
            if let Some(captures) = &paragraph_match {
                let name = group(captures, 1).to_uppercase();
                if !NON_PARAGRAPH_LABELS.contains(&name.as_str()) {
                    paragraphs.push(ParsedParagraph {
                        name,
                        section: current_section.clone(),
                        ordinal: paragraphs.len() as i64,
                        evidence: evidence_at(&relative, &lines, &offsets, &byte_lengths, index, None),
                        statements: Vec::new(),
                    });
                    current_paragraph = Some(paragraphs.len() - 1);
                    index += 1;
                    continue;
                }
            }
            if let Some(current) = current_paragraph {
                let mut end_index = index;
                let mut statement_text = code.to_string();
                if patterns.exec_block.is_match(&upper).unwrap_or(false)
                    && !upper.contains("END-EXEC")
                {
                    let mut block: Vec<String> = vec![code.to_string()];
                    while end_index + 1 < lines.len() {
                        end_index += 1;
                        block.push(code_text(lines[end_index], &source_format).to_string());
                        if lines[end_index].to_uppercase().contains("END-EXEC") {
                            break;
                        }
                    }
                    statement_text = block.join("\n");
                }
                let degraded = (index..=end_index).any(|line| degraded_lines.contains(&(line as i64)));
                if let Some(fact) = statement(
                    patterns,
                    &statement_text,
                    evidence_at(&relative, &lines, &offsets, &byte_lengths, index, Some(end_index)),
                    degraded,
                ) {
                    paragraphs[current].statements.push(fact);
                }
                index = end_index + 1;
                continue;
            }
        }
        index += 1;
    }

    let mut bindings: Vec<ParsedFileBinding> = Vec::new();
    for (name, binding) in selects.iter() {
        let mut binding = binding.clone();
        binding.has_description = described.contains(name);
        bindings.push(binding);
    }
    // selects đã BTreeMap → thứ tự key tăng dần khớp `sorted(selects.items())`.
    for name in described.iter().filter(|name| !selects.contains_key(*name)) {
        let evidence = data_items
            .iter()
            .find(|item| item.storage == "FILE")
            .map(|item| item.evidence.clone())
            .unwrap_or_else(|| SourceEvidence::new(relative.clone(), 1));
        bindings.push(ParsedFileBinding {
            name: name.clone(),
            evidence,
            assignment: String::new(),
            has_description: true,
        });
    }
    for copy in &copies {
        if !copy.replacing.is_empty() {
            diagnostics.push(
                Diagnostic::new(
                    "COBOL_COPY_REPLACING_PARTIAL",
                    format!("COPY {} REPLACING is retained but substitution is not applied", copy.name),
                    "warning",
                )
                .with_evidence(copy.evidence.clone())
                .with_details(json!({"replacement": copy.replacing})),
            );
        }
    }

    Ok(ParsedFile {
        path: relative,
        program_name,
        source_format,
        dialect,
        encoding,
        is_copybook,
        divisions: dedupe_preserve(divisions),
        sections: dedupe_preserve(sections),
        paragraphs,
        data_items,
        copies,
        file_bindings: bindings,
        diagnostics,
        tree_error_count: error_count,
    })
}

fn dedupe_preserve(values: Vec<String>) -> Vec<String> {
    let mut seen = std::collections::BTreeSet::new();
    let mut out = Vec::new();
    for value in values {
        if seen.insert(value.clone()) {
            out.push(value);
        }
    }
    out
}

/// `parse_paths` — đầu vào đã sort (iter_cobol_files trả sorted; copybook roots
/// merge rồi `sorted(set(...))` ở pipeline).
pub fn parse_paths(
    patterns: &Patterns,
    paths: &[PathBuf],
    root: &Path,
    parser: &mut LoadedParser,
) -> Result<Vec<ParsedFile>, String> {
    let mut sorted: Vec<PathBuf> = paths.to_vec();
    sorted.sort_by(|a, b| compare_path_parts(a, b));
    let mut parsed = Vec::new();
    // Python dừng ở exception đầu tiên (UnicodeDecodeError lan lên main).
    for path in sorted {
        parsed.push(parse_file(patterns, &path, root, parser)?);
    }
    Ok(parsed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixed_format_indicator_column() {
        assert_eq!(code_text("      * comment", "fixed"), "");
        assert_eq!(code_text("      / comment", "fixed"), "");
        assert_eq!(code_text("       MOVE A TO B", "fixed"), "MOVE A TO B");
        assert_eq!(code_text("       MOVE A TO B", "free"), "       MOVE A TO B");
    }

    #[test]
    fn statement_classification() {
        let patterns = Patterns::new();
        let evidence = SourceEvidence::new("a.cbl", 1);
        let call = statement(&patterns, "CALL \"SUBPROG\" USING WS", evidence.clone(), false).unwrap();
        assert_eq!(call.kind, "call");
        assert_eq!(call.properties["target"], json!("SUBPROG"));
        assert_eq!(call.properties["literal"], json!(true));

        let perform = statement(&patterns, "PERFORM INIT-PARA THRU INIT-EXIT", evidence.clone(), false).unwrap();
        assert_eq!(perform.kind, "perform");
        assert_eq!(perform.properties["through"], json!("INIT-EXIT"));

        let goto = statement(&patterns, "GO TO STEP-A STEP-B DEPENDING ON WS-STATUS", evidence, false).unwrap();
        assert_eq!(goto.kind, "goto");
        assert_eq!(goto.properties["dynamic"], json!(true));
        assert_eq!(goto.properties["targets"], json!(["STEP-A", "STEP-B"]));
    }
}

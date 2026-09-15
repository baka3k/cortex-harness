//! `collect_messages_for_parser` — port của `_collect_messages_for_parser` Python.
//!
//! Scan source files (filtered by parser extensions), chạy detector qua từng
//! dòng để detect call-site, sinh `MessageRecord`. Kết quả dedupe theo `id`
//! giữ record có confidence cao hơn.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use once_cell::sync::Lazy;
use regex::Regex;

use crate::util;

use super::detectors::{looks_endpoint, unquote, MessageDetector};
use super::extensions::{file_matches_parser, parser_extensions};
use super::record::MessageRecord;

static CALL_PATTERN: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?P<callee>[A-Za-z_][A-Za-z0-9_:.>]*)\s*\((?P<args>[^)]*)\)").expect("static regex")
});

/// Split comma-separated call args, respecting nested brackets and quotes —
/// port of `_split_args` Python.
pub fn split_args(text: &str) -> Vec<String> {
    let mut args: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut depth: i32 = 0;
    let mut in_quote: Option<char> = None;
    let mut escape = false;
    for ch in text.chars() {
        if in_quote.is_some() {
            current.push(ch);
            if escape {
                escape = false;
                continue;
            }
            if ch == '\\' {
                escape = true;
                continue;
            }
            if Some(ch) == in_quote {
                in_quote = None;
            }
            continue;
        }
        match ch {
            '"' | '\'' => {
                in_quote = Some(ch);
                current.push(ch);
            }
            '(' | '[' | '{' => {
                depth += 1;
                current.push(ch);
            }
            ')' | ']' | '}' => {
                if depth > 0 {
                    depth -= 1;
                }
                current.push(ch);
            }
            ',' if depth == 0 => {
                let token = current.trim().to_string();
                if !token.is_empty() {
                    args.push(token);
                }
                current.clear();
            }
            _ => current.push(ch),
        }
    }
    let token = current.trim().to_string();
    if !token.is_empty() {
        args.push(token);
    }
    args
}

fn last_segment(callee: &str) -> String {
    // Split on `.`, `:`, `>` and take the last lowercased segment.
    let mut last = String::new();
    for ch in callee.chars() {
        if matches!(ch, '.' | ':' | '>') {
            last.clear();
        } else {
            last.push(ch);
        }
    }
    last.to_ascii_lowercase()
}

/// Pre-compute sender per-line by walking all lines of the file once.
fn extract_sender_by_line(lines: &[String], detector: &dyn MessageDetector) -> Vec<String> {
    let mut mapping: Vec<String> = Vec::with_capacity(lines.len());
    let mut active = String::new();
    for (idx, line) in lines.iter().enumerate() {
        let stripped = line.trim();
        if !stripped.is_empty()
            && let Some(candidate) = detector.extract_sender(stripped) {
                active = candidate;
            }
        // Python keeps an entry per line (including blanks); here we mirror
        // that for parity with `_extract_sender_by_line`.
        mapping.push(active.clone());
        let _ = idx;
    }
    mapping
}

fn iter_parser_files(root: &Path, parser: &str) -> Vec<PathBuf> {
    let exts = match parser_extensions().get(parser) {
        Some(exts) => exts.clone(),
        None => return Vec::new(),
    };
    let mut files: Vec<PathBuf> = Vec::new();
    for path in walk_files(root) {
        let lower = path
            .file_name()
            .map(|n| n.to_string_lossy().to_ascii_lowercase())
            .unwrap_or_default();
        if exts.iter().any(|ext| lower.ends_with(ext)) {
            files.push(path);
        }
    }
    files.sort();
    files
}

fn walk_files(root: &Path) -> Vec<PathBuf> {
    // Lightweight manual walker — skip VCS / node_modules / .git / etc.
    // Mirrors `os.walk(root)` but ignores obvious noise directories.
    let mut stack = vec![root.to_path_buf()];
    let mut out = Vec::new();
    let skip_dirs = [
        ".git",
        "node_modules",
        "target",
        ".venv",
        "__pycache__",
        ".cortex",
        ".cortext-harness",
    ];
    while let Some(dir) = stack.pop() {
        let entries = match std::fs::read_dir(&dir) {
            Ok(e) => e,
            Err(_) => continue,
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().to_string();
            let file_type = match entry.file_type() {
                Ok(t) => t,
                Err(_) => continue,
            };
            if file_type.is_dir() {
                if skip_dirs.iter().any(|skip| name == *skip) {
                    continue;
                }
                stack.push(path);
            } else if file_type.is_file() {
                out.push(path);
            }
        }
    }
    out.sort();
    out
}

/// Normalize a relative/absolute path inside `root` to a forward-slash
/// relative path; returns None when outside `root`.
pub fn normalize_rel_path(root: &Path, value: &str) -> Option<String> {
    let raw = value.trim();
    if raw.is_empty() {
        return None;
    }
    let normalized_root = util::realpath(&util::path_to_string(root));
    let text = raw.replace('\\', "/");
    let path = Path::new(&text);
    if path.is_absolute() {
        let abs = util::realpath(&text);
        let rel = match abs.strip_prefix(&normalized_root) {
            Ok(r) => r,
            Err(_) => return None,
        };
        let rel = util::path_to_string(rel);
        if rel.starts_with("../") || rel == ".." {
            return None;
        }
        return Some(rel);
    }
    let rel = util::normalize_project_path(root, &text)?;
    Some(rel.replace('\\', "/"))
}

/// Public entry point — collect `MessageRecord`s for a given parser. Mirrors
/// Python `collect_messages_for_parser`.
pub fn collect_messages_for_parser(
    root: &Path,
    parser: &str,
    project_id: &str,
    language: &str,
    target_files: Option<&[String]>,
) -> Result<Vec<MessageRecord>, String> {
    if !parser_extensions().contains_key(parser) {
        return Err(format!("Unsupported parser for message scan: {parser}"));
    }
    let detector = super::detectors::get_detector(parser);
    let normalized_root = util::realpath(&util::path_to_string(root));
    let files: Vec<PathBuf> = if let Some(targets) = target_files {
        let mut collected: Vec<PathBuf> = Vec::new();
        for raw in targets {
            let Some(rel) = normalize_rel_path(&normalized_root, raw) else { continue };
            if !file_matches_parser(&rel, parser) {
                continue;
            }
            let abs = normalized_root.join(&rel);
            if abs.is_file() {
                collected.push(abs);
            }
        }
        collected.sort();
        collected.dedup();
        collected
    } else {
        iter_parser_files(&normalized_root, parser)
    };

    let keywords: BTreeMap<&'static str, ()> =
        detector.keywords().iter().map(|k| (*k, ())).collect();
    let mut collected: BTreeMap<String, MessageRecord> = BTreeMap::new();
    let language = if language.is_empty() { parser } else { language };

    for abs_path in &files {
        let rel_path = match abs_path.strip_prefix(&normalized_root) {
            Ok(p) => util::path_to_string(p).replace('\\', "/"),
            Err(_) => continue,
        };
        let content = match std::fs::read_to_string(abs_path) {
            Ok(c) => c,
            Err(_) => continue,
        };
        let lines: Vec<String> = content.lines().map(str::to_string).collect();
        let sender_map = extract_sender_by_line(&lines, detector);
        for (line_idx, line_text) in lines.iter().enumerate() {
            let stripped = line_text.trim();
            if stripped.is_empty() {
                continue;
            }
            let line_no = (line_idx + 1) as u32;
            for caps in CALL_PATTERN.captures_iter(stripped) {
                let callee = caps.name("callee").map(|m| m.as_str().trim()).unwrap_or("");
                let args_text = caps.name("args").map(|m| m.as_str()).unwrap_or("");
                let short_name = last_segment(callee);
                if !keywords.contains_key(short_name.as_str()) {
                    continue;
                }
                let args = split_args(args_text);
                let (message_name, receiver, payload, explanation) =
                    detector.extract_fields(callee, &args);
                if message_name.is_empty() {
                    continue;
                }
                let sender_raw = sender_map
                    .get(line_idx)
                    .map(String::as_str)
                    .unwrap_or("")
                    .trim();
                let sender_fallback = Path::new(&rel_path)
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_default();
                let sender = if sender_raw.is_empty() { sender_fallback } else { sender_raw.to_string() };

                // Confidence calculation matches Python:
                //   0.55 + (unquote(args[0]) ? 0.2 : 0) + (sender ? 0.1 : 0)
                //   + (receiver ? 0.1 : 0), capped at 0.99
                let mut confidence: f32 = 0.55;
                if let Some(first) = args.first()
                    && unquote(first).is_some() {
                        confidence += 0.2;
                    }
                if !sender.is_empty() {
                    confidence += 0.1;
                }
                if !receiver.is_empty() {
                    confidence += 0.1;
                }
                if looks_endpoint(args.first().map(String::as_str).unwrap_or("")) {
                    confidence += 0.0;
                }
                confidence = confidence.min(0.99);
                let record = MessageRecord::new(
                    project_id,
                    language,
                    &rel_path,
                    line_no,
                    &sender,
                    &receiver,
                    &message_name,
                    &payload,
                    &explanation,
                    confidence,
                );
                let existing = collected.get(&record.id);
                let replaced = match existing {
                    Some(prev) => record.confidence > prev.confidence,
                    None => true,
                };
                if replaced {
                    collected.insert(record.id.clone(), record);
                }
            }
        }
    }
    Ok(collected.into_values().collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn split_args_handles_nested_brackets_and_quotes() {
        // `_split_args` is called with the captured `args` group (text inside
        // parens), not the full callee + parens. The leading `(` and closing
        // `)` are stripped before invocation.
        let args = split_args(r#""a,b", [1, 2], {x:1}"#);
        assert_eq!(args, vec![r#""a,b""#, r#"[1, 2]"#, r#"{x:1}"#]);
    }

    #[test]
    fn last_segment_uses_last_dot_or_arrow() {
        assert_eq!(last_segment("a.b.c"), "c");
        assert_eq!(last_segment("Foo::Bar"), "bar");
        assert_eq!(last_segment("eventBus>Publish"), "publish");
        assert_eq!(last_segment(""), "");
    }

    #[test]
    fn normalize_rel_path_absolute_outside_returns_none() {
        let root = std::env::temp_dir();
        let outside = root.join("..").join("elsewhere");
        assert!(normalize_rel_path(&root, &outside.to_string_lossy()).is_none());
    }
}
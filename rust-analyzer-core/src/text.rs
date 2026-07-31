//! Zero-copy text extraction utilities.
//!
//! All string operations work on `&[u8]` source buffers; nodes are referenced
//! as `(start, end)` byte ranges and only decoded to UTF-8 when needed.

use tree_sitter::Node;

/// Get a node's text as `&str` by slicing into the source buffer (zero copy).
#[inline]
pub fn node_text<'a>(node: Node, source: &'a [u8]) -> &'a str {
    let range = node.byte_range();
    std::str::from_utf8(&source[range.start..range.end]).unwrap_or("")
}

/// Decode a node's text as owned `String` (allocates; used when escaping caller lifetime).
#[inline]
pub fn node_text_owned(node: Node, source: &[u8]) -> String {
    let range = node.byte_range();
    String::from_utf8_lossy(&source[range.start..range.end]).into_owned()
}

/// Decode a substring of source bytes as UTF-8 `String` (lossy).
#[inline]
pub fn lossy_bytes_to_string(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

/// Find the first identifier-like substring `[A-Za-z_][A-Za-z0-9_:]*`.
pub fn first_identifier_str(text: &str) -> Option<&str> {
    let bytes = text.as_bytes();
    for (i, &b) in bytes.iter().enumerate() {
        if b == b'_' || b.is_ascii_alphabetic() {
            let mut end = i + 1;
            while end < bytes.len()
                && (bytes[end] == b'_'
                    || bytes[end].is_ascii_alphanumeric()
                    || bytes[end] == b':')
            {
                end += 1;
            }
            return Some(&text[i..end]);
        }
    }
    None
}

/// Strip C++ template arguments (`<...>`) — no recursion; handles one level.
pub fn strip_template_args(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'<' {
            let mut depth = 1i32;
            let mut j = i + 1;
            while j < bytes.len() && depth > 0 {
                if bytes[j] == b'<' {
                    depth += 1;
                } else if bytes[j] == b'>' {
                    depth -= 1;
                }
                j += 1;
            }
            i = j;
        } else {
            out.push(bytes[i] as char);
            i += 1;
        }
    }
    out
}

/// Normalize a callee name (strip templates, member prefix, ref/deref markers).
pub fn normalize_call_name(text: &str) -> String {
    let cleaned = strip_template_args(text);
    let cleaned = cleaned.replace("this->", "");
    let cleaned = cleaned.replace("->", ".");
    let cleaned = cleaned.replace(['&', '*'], "");
    let cleaned = cleaned.trim();
    if let Some(idx) = cleaned.rfind('.') {
        cleaned[idx + 1..].trim().to_string()
    } else {
        cleaned.to_string()
    }
}

/// Normalize a type signature (collapse whitespace).
pub fn normalize_type_signature(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut last_space = false;
    for ch in text.chars() {
        if ch.is_whitespace() {
            if !last_space {
                out.push(' ');
                last_space = true;
            }
        } else {
            out.push(ch);
            last_space = false;
        }
    }
    out.trim().to_string()
}

/// Pull the first identifier-shaped name from arbitrary text.
pub fn extract_base_type(type_text: &str) -> Option<String> {
    let cleaned = strip_template_args(type_text);
    let mut buf = String::with_capacity(cleaned.len());
    for word in cleaned.split(|c: char| !c.is_alphanumeric() && c != '_' && c != ':') {
        if word.is_empty() {
            continue;
        }
        // Drop storage/qualifier keywords
        match word {
            "const" | "volatile" | "mutable" | "static" | "extern" | "register"
            | "inline" | "struct" | "class" | "enum" | "typename" => continue,
            _ => {
                if !buf.is_empty() {
                    buf.push(' ');
                }
                buf.push_str(word);
            }
        }
    }
    first_identifier_str(&buf).map(|s| s.to_string())
}

/// Generate a stable hash for use in ID generation.
pub fn stable_point_id(text: &str) -> String {
    // FxHash-style simple hasher; deterministic across runs.
    let mut h: u64 = 14695981039346656037;
    for b in text.as_bytes() {
        h ^= *b as u64;
        h = h.wrapping_mul(1099511628211);
    }
    format!("{:016x}", h)
}

/// Walk back through preceding siblings and include contiguous comment nodes
/// to build a snippet. Mirrors `_node_snippet` in the Python analyzer.
pub fn node_snippet(node: Node, source: &[u8]) -> (String, u32, u32) {
    let mut start_node = node;
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if p.kind() == "comment" {
            start_node = p;
            prev = p.prev_sibling();
        } else {
            break;
        }
    }
    let snippet = node_text(start_node, source);
    let start_line = start_node.start_position().row as u32 + 1;
    let end_line = node.end_position().row as u32 + 1;
    (snippet.to_string(), start_line, end_line)
}

/// Collect leading comment block (line `//` or block `/* ... */`).
pub fn extract_leading_comment(node: Node, source: &[u8]) -> String {
    let mut prev = node.prev_sibling();
    let mut comments = Vec::new();
    while let Some(p) = prev {
        if p.kind() == "comment" {
            comments.push(node_text(p, source).trim().to_string());
            prev = p.prev_sibling();
        } else {
            break;
        }
    }
    comments.reverse();
    comments.join("\n")
}

/// Collect file-level leading comment.
pub fn extract_file_comment(root: tree_sitter::Node, source: &[u8]) -> String {
    let mut parts = Vec::new();
    for child in root.children(&mut root.walk()) {
        if child.kind() == "comment" {
            let text = node_text(child, source).trim();
            if !text.is_empty() {
                parts.push(text.to_string());
            }
            continue;
        }
        if child.is_named() {
            break;
        }
    }
    parts.join("\n")
}

/// Extract `#include` paths from file text.
pub fn extract_includes(code: &str) -> Vec<String> {
    let mut out = Vec::new();
    let bytes = code.as_bytes();
    let mut i = 0;
    while i + 8 <= bytes.len() {
        if &bytes[i..i + 8] == b"#include" {
            let mut j = i + 8;
            while j < bytes.len() && (bytes[j] == b' ' || bytes[j] == b'\t') {
                j += 1;
            }
            if j < bytes.len() && (bytes[j] == b'"' || bytes[j] == b'<') {
                let open = bytes[j];
                let close = if open == b'"' { b'"' } else { b'>' };
                j += 1;
                let start = j;
                while j < bytes.len() && bytes[j] != close && bytes[j] != b'\n' {
                    j += 1;
                }
                let path = std::str::from_utf8(&bytes[start..j]).unwrap_or("").trim().to_string();
                if !path.is_empty() {
                    out.push(path);
                }
            }
        }
        i += 1;
    }
    out
}

/// Extract `#define` macro names + body.
pub fn extract_macros(code: &str) -> std::collections::HashMap<String, String> {
    let mut out = std::collections::HashMap::new();
    let bytes = code.as_bytes();
    let mut i = 0;
    while i + 7 <= bytes.len() {
        if &bytes[i..i + 7] == b"#define" {
            let mut j = i + 7;
            while j < bytes.len() && (bytes[j] == b' ' || bytes[j] == b'\t') {
                j += 1;
            }
            let name_start = j;
            while j < bytes.len() && (bytes[j].is_ascii_alphanumeric() || bytes[j] == b'_') {
                j += 1;
            }
            if j > name_start {
                let name = std::str::from_utf8(&bytes[name_start..j]).unwrap_or("").to_string();
                // body = rest of line, ignoring leading whitespace
                let body_start = j;
                let mut body_end = body_start;
                while body_end < bytes.len() && bytes[body_end] != b'\n' {
                    body_end += 1;
                }
                let body = std::str::from_utf8(&bytes[body_start..body_end])
                    .unwrap_or("")
                    .trim()
                    .to_string();
                out.insert(name, body);
            }
        }
        i += 1;
    }
    out
}

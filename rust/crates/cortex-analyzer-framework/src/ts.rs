//! Tree-sitter helpers dùng chung — khớp hành vi `tree_sitter` Python:
//! decode `errors="ignore"` (drop invalid bytes), node text, cursor walk,
//! snippet kèm leading comments.

use tree_sitter::Node;

/// Decode bytes như Python `decode("utf-8", errors="ignore")` — bỏ byte lỗi.
pub fn decode_ignore(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len());
    let mut rest = bytes;
    while !rest.is_empty() {
        match std::str::from_utf8(rest) {
            Ok(text) => {
                out.push_str(text);
                break;
            }
            Err(error) => {
                let valid = error.valid_up_to();
                if let Ok(text) = std::str::from_utf8(&rest[..valid]) {
                    out.push_str(text);
                }
                let skip = error.error_len().unwrap_or(rest.len() - valid);
                rest = &rest[valid + skip..];
            }
        }
    }
    out
}

pub fn node_text(node: Node, source: &[u8]) -> String {
    decode_ignore(&source[node.start_byte()..node.end_byte()])
}

/// `_line_from_byte` — 1-based line number.
pub fn line_from_byte(source: &[u8], byte_index: usize) -> usize {
    source[..byte_index].iter().filter(|&&b| b == b'\n').count() + 1
}

/// `_node_snippet` — text tính từ sau chuỗi leading comments, kèm start/end line.
pub fn node_snippet(node: Node, source: &[u8]) -> (String, usize, usize) {
    let mut start_byte = node.start_byte();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if p.kind() == "comment" {
            start_byte = p.start_byte();
            prev = p.prev_sibling();
        } else {
            break;
        }
    }
    let snippet = decode_ignore(&source[start_byte..node.end_byte()]);
    let start_line = line_from_byte(source, start_byte);
    let end_line = node.end_position().row + 1;
    (snippet, start_line, end_line)
}

/// `_find_nodes_by_type` — cursor walk (pre-order) thu mọi node đúng kind.
pub fn find_nodes_by_type<'tree>(root: Node<'tree>, kind: &str) -> Vec<Node<'tree>> {
    let mut found = Vec::new();
    let mut cursor = root.walk();
    loop {
        if cursor.node().kind() == kind {
            found.push(cursor.node());
        }
        if cursor.goto_first_child() {
            continue;
        }
        if cursor.goto_next_sibling() {
            continue;
        }
        loop {
            if !cursor.goto_parent() {
                return found;
            }
            if cursor.goto_next_sibling() {
                break;
            }
        }
    }
}

/// `_extract_leading_comment` — chuỗi comment liền trước node (reversed join).
pub fn extract_leading_comment(node: Node, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if p.kind() == "comment" {
            let text = node_text(p, source);
            let text = text.trim();
            if !text.is_empty() {
                parts.push(text.to_string());
            }
            prev = p.prev_sibling();
        } else {
            break;
        }
    }
    if parts.is_empty() {
        return String::new();
    }
    parts.reverse();
    parts.join("\n")
}

/// `_extract_file_comment` — các comment đầu file cho tới named node đầu tiên.
pub fn extract_file_comment(root: Node, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    for child in root.children(&mut root.walk()) {
        if child.kind() == "comment" {
            let text = node_text(child, source);
            let text = text.trim();
            if !text.is_empty() {
                parts.push(text.to_string());
            }
            continue;
        }
        if child.is_named() {
            break;
        }
    }
    if parts.is_empty() {
        return String::new();
    }
    parts.join("\n")
}

/// Python `re.sub(r"\s+", " ", text).strip()`.
pub fn normalize_ws(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut in_ws = false;
    for c in text.chars() {
        if c.is_whitespace() {
            in_ws = true;
        } else {
            if in_ws && !out.is_empty() {
                out.push(' ');
            }
            in_ws = false;
            out.push(c);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decode_ignore_drops_invalid_bytes() {
        // Python: b'a\xffb'.decode('utf-8', 'ignore') == 'ab'
        assert_eq!(decode_ignore(&[b'a', 0xff, b'b']), "ab");
        assert_eq!(decode_ignore("héllo".as_bytes()), "héllo");
    }

    #[test]
    fn normalize_ws_collapses() {
        assert_eq!(normalize_ws("  from   x  import y "), "from x import y");
    }
}

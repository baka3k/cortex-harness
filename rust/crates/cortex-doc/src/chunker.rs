//! Port of `doc-tiny/graphrag_ingest_langextract.py::split_paragraphs`.
//!
//! Byte-compatible paragraph chunking: split on blank-line runs
//! (`re.split(r"\n\s*\n+", text)`), strip each piece, drop empties, and
//! hard-wrap over-long paragraphs on `max_chars` boundaries.

/// Mirror of Python's `str.strip()` (Unicode whitespace plus the C0 file
/// separators `\x1c`-`\x1f` that Python treats as whitespace but Rust does not).
pub fn py_trim(s: &str) -> &str {
    s.trim_matches(|c: char| c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}'))
}

/// `re.split(r"\n\s*\n+", text)` equivalent via the `regex` crate.
fn split_blank_lines(text: &str) -> Vec<&str> {
    use regex::Regex;
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(r"\n\s*\n+").expect("valid regex"));
    re.split(text).collect()
}

/// Port of `split_paragraphs(text, max_chars)`.
pub fn split_paragraphs(text: &str, max_chars: usize) -> Vec<String> {
    let mut chunks = Vec::new();
    for piece in split_blank_lines(text) {
        let para = py_trim(piece);
        if para.is_empty() {
            continue;
        }
        if para.chars().count() <= max_chars {
            chunks.push(para.to_string());
        } else {
            // Python slices paragraphs by character index:
            // `para[i : i + max_chars].strip()`.
            let chars: Vec<char> = para.chars().collect();
            let mut start = 0;
            while start < chars.len() {
                let end = (start + max_chars).min(chars.len());
                let chunk: String = chars[start..end].iter().collect();
                let trimmed = py_trim(&chunk);
                if !trimmed.is_empty() {
                    chunks.push(trimmed.to_string());
                }
                start = end;
            }
        }
    }
    chunks
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn basic_split_matches_python() {
        assert_eq!(
            split_paragraphs("Para one.\n\nPara two.\n\n \nPara three.", 1200),
            vec!["Para one.", "Para two.", "Para three."]
        );
    }

    #[test]
    fn long_paragraph_hard_wraps() {
        // Python golden: split_paragraphs("A"*25 + " B"*5, 10)
        let text = format!("{} {}", "A".repeat(25), "B B B B B");
        assert_eq!(
            split_paragraphs(&text, 10),
            vec!["AAAAAAAAAA", "AAAAAAAAAA", "AAAAA B B", "B B B"]
        );
    }

    #[test]
    fn unicode_split() {
        assert_eq!(
            split_paragraphs("Xin chào thế giới.\n\n\n\nTiếng Việt có dấu.", 1200),
            vec!["Xin chào thế giới.", "Tiếng Việt có dấu."]
        );
    }

    #[test]
    fn tabs_and_empty() {
        assert_eq!(
            split_paragraphs("one\ttwo\n  \nthree", 1200),
            vec!["one\ttwo", "three"]
        );
        assert!(split_paragraphs("", 100).is_empty());
    }
}

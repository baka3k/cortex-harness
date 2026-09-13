//! Helpers tái tạo chính xác semantic chuẩn thư viện Python cho các chỗ mà
//! parser Python phụ thuộc (offsets theo CHAR không phải byte, `str.find`
//! clamp âm, `splitlines` với full boundary set, `splitext` bỏ leading dots,
//! `json.dumps(..., ensure_ascii=True)` cho `members`/`base_interfaces`).

/// Python `str.decode("utf-8", errors="ignore")` — byte invalid bị DROP
/// (khác `from_utf8_lossy` thay bằng U+FFFD).
pub fn decode_utf8_ignore(bytes: &[u8]) -> String {
    let mut out = String::new();
    let mut rest = bytes;
    loop {
        match std::str::from_utf8(rest) {
            Ok(text) => {
                out.push_str(text);
                return out;
            }
            Err(error) => {
                let valid = &rest[..error.valid_up_to()];
                out.push_str(std::str::from_utf8(valid).unwrap_or(""));
                let skip = error.error_len().unwrap_or(rest.len() - error.valid_up_to());
                rest = &rest[error.valid_up_to() + skip..];
            }
        }
    }
}

/// Python `str.splitlines()` — full boundary set (\n, \r, \r\n, \v, \f,
/// \x1c, \x1d, \x1e, \x85, \u2028, \u2029); không giữ terminator; dòng cuối
/// không đưa vào khi rỗng.
pub fn py_splitlines(text: &str) -> Vec<Vec<char>> {
    let mut lines: Vec<Vec<char>> = Vec::new();
    let mut current: Vec<char> = Vec::new();
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        let is_boundary = matches!(
            c,
            '\n' | '\r' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}'
                | '\u{2028}' | '\u{2029}'
        );
        if is_boundary {
            if c == '\r' && chars.peek() == Some(&'\n') {
                chars.next();
            }
            lines.push(std::mem::take(&mut current));
        } else {
            current.push(c);
        }
    }
    if !current.is_empty() {
        lines.push(current);
    }
    lines
}

/// Python `str.splitlines()` trả `Vec<String>` (phiên bản tiện dụng).
pub fn py_splitlines_str(text: &str) -> Vec<String> {
    py_splitlines(text)
        .into_iter()
        .map(|chars| chars.into_iter().collect())
        .collect()
}

/// Python `str.find(sub, start)` trên slice CHAR: start âm clamp về 0,
/// start > len → -1, needle rỗng → start (đã clamp) khi start <= len.
pub fn py_find_chars(haystack: &[char], needle: &[char], start: i64) -> i64 {
    let start = if start < 0 { 0 } else { start };
    let s = start as usize;
    if s > haystack.len() {
        return -1;
    }
    if needle.is_empty() {
        return s as i64;
    }
    if needle.len() > haystack.len() - s {
        return -1;
    }
    for i in s..=(haystack.len() - needle.len()) {
        if &haystack[i..i + needle.len()] == needle {
            return i as i64;
        }
    }
    -1
}

/// Python `os.path.splitext` (posix) — bỏ qua các dấu chấm LEADING của basename:
/// `.sln` → ext "", `a..sln` → (".a.", ".sln")... đúng thứ tự CPython
/// `_splitext` (skip all leading dots sau separator).
pub fn py_splitext(path: &str) -> (String, String) {
    let sep_index = path.rfind('/');
    let dot_index = path.rfind('.');
    if let Some(dot) = dot_index
        && sep_index.is_none_or(|sep| dot > sep)
    {
        let mut filename_index = sep_index.map_or(0, |sep| sep + 1);
        while filename_index < dot {
            if path.as_bytes()[filename_index] != b'.' {
                return (path[..dot].to_string(), path[dot..].to_string());
            }
            filename_index += 1;
        }
    }
    (path.to_string(), String::new())
}

/// Python `json.dumps` cho MỘT string với `ensure_ascii=True` (default) —
/// escape ", \\, control chars (\b \f \n \r \t + \u00xx), non-ASCII thành
/// \uXXXX ( surrogate pair cho astral).
pub fn py_json_escape_string(value: &str) -> String {
    let mut out = String::with_capacity(value.len() + 2);
    out.push('"');
    for c in value.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c if (c as u32) <= 0x7f => out.push(c),
            c => {
                let code = c as u32;
                if code <= 0xffff {
                    out.push_str(&format!("\\u{:04x}", code));
                } else {
                    let v = code - 0x10000;
                    let hi = 0xd800 + (v >> 10);
                    let lo = 0xdc00 + (v & 0x3ff);
                    out.push_str(&format!("\\u{hi:04x}\\u{lo:04x}"));
                }
            }
        }
    }
    out.push('"');
    out
}

/// `json.dumps(list_of_str)` — `["a", "b"]`; list rỗng → `[]`.
pub fn py_json_dumps_str_list(items: &[String]) -> String {
    if items.is_empty() {
        return "[]".to_string();
    }
    let parts: Vec<String> = items.iter().map(|s| py_json_escape_string(s)).collect();
    format!("[{}]", parts.join(", "))
}

/// `json.dumps(list_of_tuples)` — `[["A", "1"], ["B", ""]]` (separator mặc
/// định của Python là `", "` — KHÔNG dùng serde_json vì compact thiếu space).
pub fn py_json_dumps_pair_list(items: &[(String, String)]) -> String {
    if items.is_empty() {
        return "[]".to_string();
    }
    let parts: Vec<String> = items
        .iter()
        .map(|(a, b)| format!("[{}, {}]", py_json_escape_string(a), py_json_escape_string(b)))
        .collect();
    format!("[{}]", parts.join(", "))
}

/// Python `str.rstrip("\n")` (line từ splitlines đã không còn terminator —
/// no-op nhưng giữ cho đủ).
pub fn rstrip_newline(line: &str) -> &str {
    line.strip_suffix('\n').unwrap_or(line)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decode_ignore_drops_invalid_bytes() {
        // Python: b"a\xffb".decode("utf-8", errors="ignore") == "ab"
        assert_eq!(decode_utf8_ignore(&[b'a', 0xff, b'b']), "ab");
        assert_eq!(decode_utf8_ignore("héllo".as_bytes()), "héllo");
    }

    #[test]
    fn splitlines_full_boundaries() {
        assert_eq!(py_splitlines_str("a\nb\r\nc\rd"), vec!["a", "b", "c", "d"]);
        assert_eq!(py_splitlines_str("a\n"), vec!["a"]);
        assert_eq!(py_splitlines_str(""), Vec::<String>::new());
        // Python: "\n".splitlines() == [""] (một dòng rỗng, không phải 2)
        assert_eq!(py_splitlines_str("\n"), vec![""]);
        assert_eq!(py_splitlines_str("a\u{2028}b"), vec!["a", "b"]);
    }

    #[test]
    fn find_python_semantics() {
        let hay: Vec<char> = "hello world".chars().collect();
        assert_eq!(py_find_chars(&hay, &"world".chars().collect::<Vec<_>>(), 0), 6);
        assert_eq!(py_find_chars(&hay, &"hello".chars().collect::<Vec<_>>(), 1), -1);
        // start âm clamp về 0
        assert_eq!(py_find_chars(&hay, &"hello".chars().collect::<Vec<_>>(), -5), 0);
        // needle rỗng → start đã clamp
        assert_eq!(py_find_chars(&hay, &[], 7), 7);
        assert_eq!(py_find_chars(&hay, &[], -3), 0);
        // start > len → -1 (kể cả needle rỗng)
        assert_eq!(py_find_chars(&hay, &[], 12), -1);
        // needle dài hơn phần còn lại → -1
        assert_eq!(py_find_chars(&hay, &"worldx".chars().collect::<Vec<_>>(), 0), -1);
    }

    #[test]
    fn splitext_leading_dots() {
        assert_eq!(py_splitext("a.vb"), ("a".into(), ".vb".into()));
        assert_eq!(py_splitext("dir.v2/file"), ("dir.v2/file".into(), "".into()));
        assert_eq!(py_splitext(".sln"), (".sln".into(), "".into()));
        assert_eq!(py_splitext("..sln"), ("..sln".into(), "".into()));
        assert_eq!(py_splitext("a..sln"), ("a.".into(), ".sln".into()));
        assert_eq!(py_splitext("a/b.c.vb"), ("a/b.c".into(), ".vb".into()));
    }

    #[test]
    fn json_ensure_ascii() {
        assert_eq!(py_json_escape_string("a\"b\\c"), "\"a\\\"b\\\\c\"");
        assert_eq!(py_json_escape_string("é"), "\"\\u00e9\"");
        assert_eq!(py_json_escape_string("\u{1f600}"), "\"\\ud83d\\ude00\"");
        assert_eq!(py_json_escape_string("\u{1}"), "\"\\u0001\"");
        assert_eq!(py_json_dumps_str_list(&["IPoint".into()]), "[\"IPoint\"]");
        assert_eq!(py_json_dumps_str_list(&[]), "[]");
        assert_eq!(
            py_json_dumps_pair_list(&[("On".into(), "1".into())]),
            "[[\"On\", \"1\"]]"
        );
    }
}

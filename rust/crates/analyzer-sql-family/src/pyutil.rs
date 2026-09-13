//! Python-compat helpers dùng chung cho SQL-family ports: hashlib hex,
//! `str.strip(chars)`, `re.escape`, `html.unescape`, `ast.literal_eval`
//! cho string literals Java/Python, splitlines.

use sha1::{Digest as Sha1Digest, Sha1};
use sha2::Sha256;

/// `hashlib.sha1(data).hexdigest()`.
pub fn sha1_hex(data: &[u8]) -> String {
    let mut hasher = Sha1::new();
    hasher.update(data);
    let digest = hasher.finalize();
    hex_encode(&digest)
}

/// `hashlib.sha256(data).hexdigest()`.
pub fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    let digest = hasher.finalize();
    hex_encode(&digest)
}

fn hex_encode(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

/// `str.strip(chars)` — strip cả 2 đầu theo tập ký tự.
pub fn strip_chars<'a>(text: &'a str, chars: &str) -> &'a str {
    text.trim_matches(|c| chars.contains(c))
}

/// `re.escape` (Python 3.7+: chỉ escape ký tự đặc biệt ASCII).
pub fn re_escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        if c.is_ascii_alphanumeric() || c == '_' {
            out.push(c);
        } else {
            // Escape mọi ký tự non-alnum bằng backslash (an toàn với regex crate).
            out.push('\\');
            out.push(c);
        }
    }
    out
}

/// Python `str.splitlines()` — tách tại \n, \r\n, \r (subset đủ cho text).
pub fn splitlines(text: &str) -> Vec<&str> {
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
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// Python `html.unescape` — subset đủ cho XML attribute values + EntityRef:
/// mọi entity dạng `&name;` (bảng XML five + HTML common) và numeric
/// `&#123;` / `&#x1F;`. Không phân biệt named-entity không có `;`
/// (HTML5 legacy) — nhận và trả nguyên văn để khớp các fixture thật
/// (mybatis XML chỉ dùng XML five trong thực tế).
pub fn html_unescape(text: &str) -> String {
    if !text.contains('&') {
        return text.to_string();
    }
    let mut out = String::with_capacity(text.len());
    let bytes = text.as_bytes();
    let mut i = 0usize;
    while i < bytes.len() {
        if bytes[i] != b'&' {
            // copy 1 char (utf-8 safe: advance to next char boundary)
            let ch_len = utf8_char_len(bytes[i]);
            out.push_str(&text[i..i + ch_len]);
            i += ch_len;
            continue;
        }
        if let Some((replacement, consumed)) = parse_entity(&text[i..]) {
            out.push_str(&replacement);
            i += consumed;
        } else {
            out.push('&');
            i += 1;
        }
    }
    out
}

fn utf8_char_len(first: u8) -> usize {
    match first {
        0x00..=0x7f => 1,
        0xc0..=0xdf => 2,
        0xe0..=0xef => 3,
        _ => 4,
    }
}

fn parse_entity(rest: &str) -> Option<(String, usize)> {
    // rest bắt đầu bằng '&'
    let semi = rest.find(';')?;
    if !(2..=32).contains(&semi) {
        // `&#;` không hợp lệ; tên entity quá dài không phải entity
        if !rest.starts_with("&#") {
            return None;
        }
    }
    let body = &rest[1..semi];
    if let Some(num) = body.strip_prefix("#x").or_else(|| body.strip_prefix("#X")) {
        if !num.is_empty() && num.chars().all(|c| c.is_ascii_hexdigit()) {
            let cp = u32::from_str_radix(num, 16).ok()?;
            return char_from_codepoint(cp).map(|c| (c.to_string(), semi + 1));
        }
        return None;
    }
    if let Some(num) = body.strip_prefix('#') {
        if !num.is_empty() && num.chars().all(|c| c.is_ascii_digit()) {
            let cp: u32 = num.parse().ok()?;
            return char_from_codepoint(cp).map(|c| (c.to_string(), semi + 1));
        }
        return None;
    }
    let mapped = named_entity(body)?;
    Some((mapped.to_string(), semi + 1))
}

fn char_from_codepoint(cp: u32) -> Option<char> {
    // Python html.unescape với cp invalid (0, surrogate) trả U+FFFD.
    if !(0x1..=0x10ffff).contains(&cp) || (0xd800..=0xdfff).contains(&cp) {
        return Some('\u{fffd}');
    }
    char::from_u32(cp)
}

/// Bảng named entities: XML five + các HTML phổ biến (Python `html4`
/// entities khớp hành vi `html.unescape` cho corpus XML thực tế).
fn named_entity(name: &str) -> Option<&'static str> {
    Some(match name {
        "amp" | "AMP" => "&",
        "lt" | "LT" => "<",
        "gt" => ">",
        "quot" | "QUOT" => "\"",
        "apos" => "'",
        "nbsp" => "\u{a0}",
        "copy" | "COPY" => "\u{a9}",
        "reg" | "REG" => "\u{ae}",
        "trade" | "TRADE" => "\u{2122}",
        "hellip" => "\u{2026}",
        "mdash" => "\u{2014}",
        "ndash" => "\u{2013}",
        "lsquo" => "\u{2018}",
        "rsquo" => "\u{2019}",
        "ldquo" => "\u{201c}",
        "rdquo" => "\u{201d}",
        "laquo" => "\u{ab}",
        "raquo" => "\u{bb}",
        "deg" => "\u{b0}",
        "plusmn" => "\u{b1}",
        "times" => "\u{d7}",
        "divide" => "\u{f7}",
        "middot" => "\u{b7}",
        "bull" => "\u{2022}",
        "dagger" => "\u{2020}",
        "Dagger" => "\u{2021}",
        "sect" => "\u{a7}",
        "para" => "\u{b6}",
        "szlig" => "\u{df}",
        "agrave" => "\u{e0}",
        "aacute" => "\u{e1}",
        "egrave" => "\u{e8}",
        "eacute" => "\u{e9}",
        "ecirc" => "\u{ea}",
        "igrave" => "\u{ec}",
        "iacute" => "\u{ed}",
        "ograve" => "\u{f2}",
        "oacute" => "\u{f3}",
        "ugrave" => "\u{f9}",
        "uacute" => "\u{fa}",
        "ccedil" => "\u{e7}",
        "ntilde" => "\u{f1}",
        "Agrave" => "\u{c0}",
        "Aacute" => "\u{c1}",
        "Egrave" => "\u{c8}",
        "Eacute" => "\u{c9}",
        "Igrave" => "\u{cc}",
        "Iacute" => "\u{cd}",
        "Ograve" => "\u{d2}",
        "Oacute" => "\u{d3}",
        "Ugrave" => "\u{d9}",
        "Uacute" => "\u{da}",
        "Ccedil" => "\u{c7}",
        "Ntilde" => "\u{d1}",
        "uuml" => "\u{fc}",
        "ouml" => "\u{f6}",
        "auml" => "\u{e4}",
        "euml" => "\u{eb}",
        "iuml" => "\u{ef}",
        "Uuml" => "\u{dc}",
        "Ouml" => "\u{d6}",
        "Auml" => "\u{c4}",
        "Euml" => "\u{cb}",
        "Iuml" => "\u{cf}",
        "micro" => "\u{b5}",
        "pound" => "\u{a3}",
        "euro" => "\u{20ac}",
        "yen" => "\u{a5}",
        "cent" => "\u{a2}",
        "curren" => "\u{a4}",
        "frac12" => "\u{bd}",
        "frac14" => "\u{bc}",
        "frac34" => "\u{be}",
        "sup1" => "\u{b9}",
        "sup2" => "\u{b2}",
        "sup3" => "\u{b3}",
        "alpha" => "\u{3b1}",
        "beta" => "\u{3b2}",
        "gamma" => "\u{3b3}",
        "delta" => "\u{3b4}",
        "pi" => "\u{3c0}",
        "sigma" => "\u{3c3}",
        "omega" => "\u{3c9}",
        "infin" => "\u{221e}",
        "ne" => "\u{2260}",
        "le" => "\u{2264}",
        "ge" => "\u{2265}",
        "larr" => "\u{2190}",
        "rarr" => "\u{2192}",
        "uarr" => "\u{2191}",
        "darr" => "\u{2193}",
        "harr" => "\u{2194}",
        _ => return None,
    })
}

/// `ast.literal_eval('"..."')` cho Java/Python string literal — unescape
/// các escape sequence chuẩn (\n \t \r \\ \" \' \xNN \uNNNN \UNNNNNNNN
/// \0.. \NNN octal). Khớp hành vi cho literals Java annotation thực tế.
pub fn unescape_string_literal(literal: &str) -> Option<String> {
    let inner = literal.strip_prefix('"')?.strip_suffix('"')?;
    let mut out = String::with_capacity(inner.len());
    let mut chars = inner.chars();
    while let Some(c) = chars.next() {
        if c != '\\' {
            out.push(c);
            continue;
        }
        let esc = chars.next()?;
        match esc {
            'n' => out.push('\n'),
            't' => out.push('\t'),
            'r' => out.push('\r'),
            'b' => out.push('\u{8}'),
            'f' => out.push('\u{c}'),
            'v' => out.push('\u{b}'),
            'a' => out.push('\u{7}'),
            '\\' => out.push('\\'),
            '"' => out.push('"'),
            '\'' => out.push('\''),
            'x' => {
                let hex: String = chars.by_ref().take(2).collect();
                let value = u32::from_str_radix(&hex, 16).ok()?;
                out.push(char::from_u32(value)?);
            }
            'u' => {
                let hex: String = chars.by_ref().take(4).collect();
                let value = u32::from_str_radix(&hex, 16).ok()?;
                out.push(char::from_u32(value)?);
            }
            'U' => {
                let hex: String = chars.by_ref().take(8).collect();
                let value = u32::from_str_radix(&hex, 16).ok()?;
                out.push(char::from_u32(value)?);
            }
            '0'..='7' => {
                let mut value = esc.to_digit(8).unwrap();
                for _ in 0..2 {
                    let next = chars.clone().next();
                    match next {
                        Some(d @ '0'..='7') => {
                            value = value * 8 + d.to_digit(8).unwrap();
                            chars.next();
                        }
                        _ => break,
                    }
                }
                out.push(char::from_u32(value)?);
            }
            other => {
                // Python literal_eval giữ nguyên escape lạ (e.g. `\q` → `\q`).
                out.push('\\');
                out.push(other);
            }
        }
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sha1_matches_python() {
        assert_eq!(sha1_hex(b"hello"), "aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d");
    }

    #[test]
    fn strip_chars_python_semantics() {
        assert_eq!(strip_chars("./a/b/.", "./"), "a/b");
        assert_eq!(strip_chars("..x//", "./"), "x");
    }

    #[test]
    fn unescape_basics() {
        assert_eq!(
            unescape_string_literal("\"a\\nb\\\"c\\\\d\"").unwrap(),
            "a\nb\"c\\d"
        );
        assert_eq!(unescape_string_literal("\"\\u00e9\"").unwrap(), "é");
    }

    #[test]
    fn html_unescape_xml_five() {
        assert_eq!(html_unescape("a &amp; b &lt;c&gt; &quot;d&quot; &apos;e'"), "a & b <c> \"d\" 'e'");
        assert_eq!(html_unescape("&#65;&#x42;"), "AB");
        assert_eq!(html_unescape("plain&thing"), "plain&thing");
    }
}

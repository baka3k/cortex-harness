//! Python str/bytes semantics cần thiết cho byte-exact parse — splitlines
//! (bao gồm \v \f \x1c-\x1e \x85 \u2028 \u2029 như `str.splitlines`), codec
//! cp1252/cp037 (bảng sinh từ CPython, xem `codec_tables.rs`), và
//! `encode(errors='replace')`.

use crate::codec_tables::{CP037_DECODE, CP037_ENCODE, CP1252_DECODE, CP1252_ENCODE};

/// Marker lỗi decode (Python UnicodeDecodeError) — không mang payload.
#[derive(Debug, Clone, Copy)]
pub struct DecodeError;

/// Python `str.splitlines()` — trả (lines, lines_keepends).
pub fn py_splitlines(text: &str) -> (Vec<&str>, Vec<&str>) {
    let char_indices: Vec<(usize, char)> = text.char_indices().collect();
    let mut lines: Vec<&str> = Vec::new();
    let mut keepends: Vec<&str> = Vec::new();
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
        // \r\n tính MỘT boundary.
        let crlf = ch == '\r' && char_indices.get(i + 1).map(|(_, c)| *c) == Some('\n');
        let end = if crlf {
            char_indices[i + 1].0 + 1
        } else {
            pos + ch.len_utf8()
        };
        lines.push(&text[start..pos]);
        keepends.push(&text[start..end.min(byte_len)]);
        i += if crlf { 2 } else { 1 };
        start = end;
    }
    if start < byte_len {
        lines.push(&text[start..]);
        keepends.push(&text[start..]);
    }
    (lines, keepends)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CobolCodec {
    Utf8,
    Cp1252,
    Cp037,
}

impl CobolCodec {
    /// `_source_codec` — utf-8-sig encode qua codec utf-8.
    pub fn from_encoding(encoding: &str) -> Self {
        match encoding {
            "utf-8-sig" | "utf-8" => Self::Utf8,
            "cp1252" => Self::Cp1252,
            "cp037" => Self::Cp037,
            other => panic!("unhandled cobol codec: {other}"),
        }
    }

    /// `str.encode(codec, errors='replace')` — char không biểu diễn được ⇒ b'?'.
    pub fn encode_replace(&self, text: &str) -> Vec<u8> {
        match self {
            Self::Utf8 => text.as_bytes().to_vec(),
            Self::Cp1252 => encode_table(text, &CP1252_ENCODE),
            Self::Cp037 => encode_table(text, &CP037_ENCODE),
        }
    }

    pub fn encode_len(&self, text: &str) -> usize {
        self.encode_replace(text).len()
    }
}

fn encode_table(text: &str, table: &[Option<char>; 256]) -> Vec<u8> {
    let mut out = Vec::with_capacity(text.len());
    for ch in text.chars() {
        let mapped = table
            .iter()
            .position(|candidate| *candidate == Some(ch));
        match mapped {
            Some(byte) => out.push(byte as u8),
            None => out.push(b'?'),
        }
    }
    out
}

/// `bytes.decode('cp1252')` — strict; CPython map 5 byte undefined sang C1.
pub fn decode_cp1252(data: &[u8]) -> Result<String, DecodeError> {
    decode_table(data, &CP1252_DECODE)
}

/// `bytes.decode('cp037')` — strict (0xFF undefined ⇒ lỗi).
pub fn decode_cp037(data: &[u8]) -> Result<String, DecodeError> {
    decode_table(data, &CP037_DECODE)
}

fn decode_table(data: &[u8], table: &[Option<char>; 256]) -> Result<String, DecodeError> {
    let mut out = String::with_capacity(data.len());
    for &byte in data {
        match table[byte as usize] {
            Some(ch) => out.push(ch),
            None => return Err(DecodeError),
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splitlines_matches_python_semantics() {
        // "a\nb" → ["a","b"]; "a\n" → ["a"] (không line rỗng cuối).
        let (lines, keep) = py_splitlines("a\nb");
        assert_eq!(lines, vec!["a", "b"]);
        assert_eq!(keep, vec!["a\n", "b"]);
        let (lines, keep) = py_splitlines("a\n");
        assert_eq!(lines, vec!["a"]);
        assert_eq!(keep, vec!["a\n"]);
        let (lines, _) = py_splitlines("x\ry");
        assert_eq!(lines, vec!["x", "y"]);
        let (lines, _) = py_splitlines("x\r\ny");
        assert_eq!(lines, vec!["x", "y"]);
        let (lines, _) = py_splitlines("x\u{0b}y\u{1c}z");
        assert_eq!(lines, vec!["x", "y", "z"]);
        let (lines, _) = py_splitlines("");
        assert!(lines.is_empty());
    }

    #[test]
    fn cp1252_strict_undefined_bytes() {
        // CPython 3.12: 5 byte undefined (81 8D 8F 90 9D) raise UnicodeDecodeError
        // ⇒ _decode_source lan ValueError ⇒ rc=2 (đúng semantics Python).
        for byte in [0x81u8, 0x8d, 0x8f, 0x90, 0x9d] {
            assert!(decode_cp1252(&[byte]).is_err(), "0x{byte:02x} must fail");
        }
        // char ngoài cp1252 → '?' (errors=replace)
        assert_eq!(CobolCodec::Cp1252.encode_replace("\u{4e2d}"), b"?".to_vec());
        assert_eq!(decode_cp1252(&[0x80]).unwrap(), "\u{20ac}");
    }

    #[test]
    fn cp037_roundtrip() {
        assert_eq!(decode_cp037(&[0xc1]).unwrap(), "A");
        assert_eq!(decode_cp037(&[0xff]).unwrap(), "\u{9f}");
        assert_eq!(CobolCodec::Cp037.encode_replace("A"), vec![0xc1]);
    }
}

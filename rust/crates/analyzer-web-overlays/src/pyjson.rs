//! Python-JSON serializer — tái tạo chính xác các biến thể `json.dumps` mà
//! overlay Python dùng, để output byte-identical:
//!
//! * `dumps_default` — `json.dumps(obj, ensure_ascii=False)` (separators mặc
//!   định `(", ", ": ")`, insertion order) → dry-run output của web overlay.
//! * `dumps_compact_sorted_ascii` — `json.dumps(obj, ensure_ascii=True,
//!   sort_keys=True, separators=(",", ":"))` → checksum + graph_property_value.
//! * `dumps_pretty_sorted_ascii` — `json.dumps(obj, ensure_ascii=True,
//!   sort_keys=True, indent=2)` → preview output của 2 overlay ASP.NET.
//!
//! Float dùng shortest round-trip (ryu) ≡ `repr(float)` của Python; chuỗi
//! escape đúng kiểu Python (`\b \f \n \r \t \" \\` + `\uXXXX`).

use serde_json::Value;

/// `json.dumps(obj, ensure_ascii=False)` — separators mặc định, giữ insertion
/// order của Map.
pub fn dumps_default(value: &Value) -> String {
    let style = Style {
        item_separator: ", ",
        kv_separator: ": ",
        ascii: false,
        sorted: false,
    };
    let mut out = String::new();
    write_value(value, &style, &mut out);
    out
}

/// `json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":"))`.
pub fn dumps_compact_sorted_ascii(value: &Value) -> String {
    let style = Style {
        item_separator: ",",
        kv_separator: ":",
        ascii: true,
        sorted: true,
    };
    let mut out = String::new();
    write_value(value, &style, &mut out);
    out
}

/// `json.dumps(obj, ensure_ascii=True, sort_keys=True, indent=2)` + `"\n"`.
pub fn dumps_pretty_sorted_ascii(value: &Value) -> String {
    let mut out = String::new();
    write_pretty(value, 0, &mut out);
    out.push('\n');
    out
}

struct Style {
    item_separator: &'static str,
    kv_separator: &'static str,
    ascii: bool,
    sorted: bool,
}

fn write_value(value: &Value, style: &Style, out: &mut String) {
    match value {
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(style.item_separator);
                }
                write_value(item, style, out);
            }
            out.push(']');
        }
        Value::Object(map) => {
            out.push('{');
            let mut entries: Vec<(&String, &Value)> = map.iter().collect();
            if style.sorted {
                entries.sort_by(|a, b| a.0.cmp(b.0));
            }
            for (index, (key, item)) in entries.iter().enumerate() {
                if index > 0 {
                    out.push_str(style.item_separator);
                }
                write_string(key, style.ascii, out);
                out.push_str(style.kv_separator);
                write_value(item, style, out);
            }
            out.push('}');
        }
        other => write_scalar(other, style.ascii, out),
    }
}

fn write_pretty(value: &Value, depth: usize, out: &mut String) {
    match value {
        Value::Array(items) => {
            if items.is_empty() {
                out.push_str("[]");
                return;
            }
            out.push_str("[\n");
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(",\n");
                }
                indent(depth + 1, out);
                write_pretty(item, depth + 1, out);
            }
            out.push('\n');
            indent(depth, out);
            out.push(']');
        }
        Value::Object(map) => {
            if map.is_empty() {
                out.push_str("{}");
                return;
            }
            out.push_str("{\n");
            let mut entries: Vec<(&String, &Value)> = map.iter().collect();
            entries.sort_by(|a, b| a.0.cmp(b.0));
            for (index, (key, item)) in entries.iter().enumerate() {
                if index > 0 {
                    out.push_str(",\n");
                }
                indent(depth + 1, out);
                write_string(key, true, out);
                out.push_str(": ");
                write_pretty(item, depth + 1, out);
            }
            out.push('\n');
            indent(depth, out);
            out.push('}');
        }
        other => write_scalar(other, true, out),
    }
}

fn indent(depth: usize, out: &mut String) {
    for _ in 0..depth {
        out.push_str("  ");
    }
}

fn write_scalar(value: &Value, ascii: bool, out: &mut String) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(number) => out.push_str(&format_number(number)),
        Value::String(text) => write_string(text, ascii, out),
        Value::Array(_) | Value::Object(_) => unreachable!("handled by write_value"),
    }
}

fn format_number(number: &serde_json::Number) -> String {
    if let Some(int) = number.as_i64() {
        return int.to_string();
    }
    if let Some(uint) = number.as_u64() {
        return uint.to_string();
    }
    let float = number.as_f64().unwrap_or_default();
    if float.is_nan() {
        return "NaN".into();
    }
    if float.is_infinite() {
        return if float > 0.0 { "Infinity".into() } else { "-Infinity".into() };
    }
    format_float(float)
}

/// `repr(float)` của Python — shortest round-trip; `1.0` in ra `"1.0"` (luôn
/// có phần thập phân).
fn format_float(float: f64) -> String {
    let text = format!("{float}");
    if text.contains('.') || text.contains('e') || text.contains('E') {
        text
    } else {
        format!("{text}.0")
    }
}

fn write_string(text: &str, ascii: bool, out: &mut String) {
    out.push('"');
    for character in text.chars() {
        let code = character as u32;
        match character {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            character if (character as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", code));
            }
            other if !ascii || code < 0x80 => out.push(other),
            other => {
                // Python ensure_ascii: UTF-16 code units (surrogate pair cho
                // astral plane).
                let mut buffer = [0u16; 2];
                for unit in other.encode_utf16(&mut buffer) {
                    out.push_str(&format!("\\u{:04x}", unit));
                }
            }
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn dumps_default_matches_python() {
        // json.dumps({"a": [1, 2.5], "b": "x"}) == '{"a": [1, 2.5], "b": "x"}'
        let value = json!({"a": [1, 2.5], "b": "x"});
        assert_eq!(dumps_default(&value), r#"{"a": [1, 2.5], "b": "x"}"#);
    }

    #[test]
    fn floats_print_like_python_repr() {
        assert_eq!(format_float(1.0), "1.0");
        assert_eq!(format_float(0.6), "0.6");
        assert_eq!(format_float(0.85), "0.85");
    }

    #[test]
    fn ascii_escaping_uses_surrogate_pairs() {
        let value = json!("é\u{1F600}");
        assert_eq!(dumps_compact_sorted_ascii(&value), r#""\u00e9\ud83d\ude00""#);
    }

    #[test]
    fn compact_sorted_ignores_insertion_order() {
        let mut map = serde_json::Map::new();
        map.insert("b".into(), json!(1));
        map.insert("a".into(), json!(2));
        assert_eq!(dumps_compact_sorted_ascii(&Value::Object(map)), r#"{"a":2,"b":1}"#);
    }
}

//! Python-JSON serializer — tái tạo `json.dumps(..., ensure_ascii=True,
//! sort_keys=True, separators=(",", ":"))` và biến thể indent=2 của Python.
//!
//! Overlay sinh `generation_id`/`payload_sha256`/artifact JSON bằng
//! `json.dumps` phía Python, nên serialization phải byte-identical:
//! * float: `repr(float)` của Python = shortest round-trip (serde_json/ryu cho
//!   cùng f64 → cùng chuỗi).
//! * `ensure_ascii=True`: non-ASCII → `\uXXXX` (surrogate pair cho astral).
//! * Escape control chars: Python dùng `\b \f \n \r \t` + `\u00XX`.

use serde_json::Value;

/// Serialize một `Value` theo `json.dumps(obj, ensure_ascii=True,
/// sort_keys=True, separators=(",", ":"))`.
pub fn dumps_compact(value: &Value) -> String {
    let mut out = String::new();
    write_value(value, true, &mut out);
    out
}

/// `json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`
/// — dùng bởi struts `_graph_value`.
pub fn dumps_compact_unicode(value: &Value) -> String {
    let mut out = String::new();
    write_value_unicode(value, true, &mut out);
    out
}

/// Serialize theo `json.dumps(obj, ensure_ascii=True, sort_keys=True,
/// indent=2)` + newline cuối (khớp `json.dump(...); handle.write("\n")`).
pub fn dumps_pretty(value: &Value) -> String {
    let mut out = String::new();
    write_pretty(value, 0, &mut out);
    out.push('\n');
    out
}

/// `json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)` + "\n".
pub fn dumps_pretty_unicode(value: &Value) -> String {
    let mut out = String::new();
    write_pretty_unicode(value, 0, &mut out);
    out.push('\n');
    out
}

/// `sort_keys=True` require object keys theo thứ tự sorted — serde_json Map
/// của crate này giữ insertion order nên caller phải build bằng `py_object`
/// (BTreeMap). Hàm này sort defensive trước khi ghi.
pub fn py_object(entries: Vec<(String, Value)>) -> Value {
    let mut map = serde_json::Map::new();
    let mut keys: Vec<(String, Value)> = entries;
    keys.sort_by(|a, b| a.0.cmp(&b.0));
    for (k, v) in keys {
        map.insert(k, v);
    }
    Value::Object(map)
}

fn write_value(value: &Value, sorted: bool, out: &mut String) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(n) => out.push_str(&format_number(n)),
        Value::String(s) => write_escaped(s, out),
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                write_value(item, sorted, out);
            }
            out.push(']');
        }
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            if sorted {
                keys.sort();
            }
            out.push('{');
            for (index, key) in keys.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                write_escaped(key, out);
                out.push(':');
                write_value(&map[*key], sorted, out);
            }
            out.push('}');
        }
    }
}

fn write_value_unicode(value: &Value, sorted: bool, out: &mut String) {
    match value {
        Value::String(s) => write_escaped_unicode(s, out),
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                write_value_unicode(item, sorted, out);
            }
            out.push(']');
        }
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            if sorted {
                keys.sort();
            }
            out.push('{');
            for (index, key) in keys.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                write_escaped_unicode(key, out);
                out.push(':');
                write_value_unicode(&map[*key], sorted, out);
            }
            out.push('}');
        }
        other => write_value(other, sorted, out),
    }
}

/// Escape như `json.dumps(..., ensure_ascii=False)`.
fn write_escaped_unicode(text: &str, out: &mut String) {
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

fn write_pretty(value: &Value, depth: usize, out: &mut String) {
    let indent = "  ".repeat(depth);
    let indent_in = "  ".repeat(depth + 1);
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
                out.push_str(&indent_in);
                write_pretty(item, depth + 1, out);
            }
            out.push('\n');
            out.push_str(&indent);
            out.push(']');
        }
        Value::Object(map) => {
            if map.is_empty() {
                out.push_str("{}");
                return;
            }
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            out.push_str("{\n");
            for (index, key) in keys.iter().enumerate() {
                if index > 0 {
                    out.push_str(",\n");
                }
                out.push_str(&indent_in);
                write_escaped(key, out);
                out.push_str(": ");
                write_pretty(&map[*key], depth + 1, out);
            }
            out.push('\n');
            out.push_str(&indent);
            out.push('}');
        }
        other => write_value(other, true, out),
    }
}

fn write_pretty_unicode(value: &Value, depth: usize, out: &mut String) {
    let indent = "  ".repeat(depth);
    let indent_in = "  ".repeat(depth + 1);
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
                out.push_str(&indent_in);
                write_pretty_unicode(item, depth + 1, out);
            }
            out.push('\n');
            out.push_str(&indent);
            out.push(']');
        }
        Value::Object(map) => {
            if map.is_empty() {
                out.push_str("{}");
                return;
            }
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            out.push_str("{\n");
            for (index, key) in keys.iter().enumerate() {
                if index > 0 {
                    out.push_str(",\n");
                }
                out.push_str(&indent_in);
                write_escaped_unicode(key, out);
                out.push_str(": ");
                write_pretty_unicode(&map[*key], depth + 1, out);
            }
            out.push('\n');
            out.push_str(&indent);
            out.push('}');
        }
        other => write_value_unicode(other, true, out),
    }
}

/// Format số như Python json: float qua repr (ryu shortest round-trip khớp);
/// serde_json tự render f64 kiểu `1.0` cho số nguyên-feel float và `1` cho i64.
fn format_number(n: &serde_json::Number) -> String {
    n.to_string()
}

/// Escape chuỗi như `json.dumps(..., ensure_ascii=True)` của Python.
fn write_escaped(text: &str, out: &mut String) {
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c if (c as u32) <= 0x7F => out.push(c),
            c => {
                let code = c as u32;
                if code <= 0xFFFF {
                    out.push_str(&format!("\\u{:04x}", code));
                } else {
                    // Surrogate pair như Python ensure_ascii.
                    let v = code - 0x10000;
                    let high = 0xD800 + (v >> 10);
                    let low = 0xDC00 + (v & 0x3FF);
                    out.push_str(&format!("\\u{:04x}\\u{:04x}", high, low));
                }
            }
        }
    }
    out.push('"');
}

/// Python `str(value)` cho JSON values (dùng ở chuỗi hệ quả như
/// `str(config.value)`, `str(item)`).
pub fn py_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => s.clone(),
        Value::Array(_) => py_repr(value),
        Value::Object(_) => py_repr(value),
    }
}

/// Python `repr(value)` — quote偏好 single-quote, dict/list/tuple dạng repr.
pub fn py_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => py_repr_str(s),
        Value::Array(items) => {
            let inner: Vec<String> = items.iter().map(py_repr).collect();
            format!("[{}]", inner.join(", "))
        }
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let inner: Vec<String> = keys
                .iter()
                .map(|k| format!("{}: {}", py_repr_str(k), py_repr(&map[k.as_str()])))
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
    }
}

/// Python repr của một chuỗi: quote ưu tiên `'`; nếu chuỗi chứa `'` mà không
/// chứa `"` thì dùng `"`. Escape `\\` và quote char; non-printable → `\xNN`/
/// `\uNNNN` (ASCII fixture đủ dùng; phần này cho key path chuẩn hoá).
pub fn py_repr_str(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::new();
    out.push(quote);
    for ch in text.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || (c as u32) == 0x7F => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// Python truthiness cho JSON values.
pub fn py_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(false),
        Value::String(s) => !s.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn matches_python_dumps() {
        assert_eq!(dumps_compact(&json!({"b": 1, "a": "x"})), r#"{"a":"x","b":1}"#);
        assert_eq!(dumps_compact(&json!("héllo")), r#""h\u00e9llo""#);
        assert_eq!(dumps_compact(&json!("emoji \u{1F600}")), "\"emoji \\ud83d\\ude00\"");
        assert_eq!(dumps_compact(&json!(1.0)), "1.0");
        assert_eq!(dumps_compact(&json!(0.72)), "0.72");
        assert_eq!(dumps_compact(&json!([true, false, null])), "[true,false,null]");
        assert_eq!(dumps_compact(&json!("\u{1}")), "\"\\u0001\"");
    }

    #[test]
    fn py_repr_and_str() {
        assert_eq!(py_str(&json!(true)), "True");
        assert_eq!(py_str(&Value::Null), "None");
        assert_eq!(py_repr(&json!({"b": 1, "a": "x"})), "{'a': 'x', 'b': 1}");
        assert_eq!(py_repr(&json!(["x", 1])), "['x', 1]");
        assert_eq!(py_repr(&json!("it's")), "\"it's\"");
        assert_eq!(py_repr(&json!("say \"hi\"")), "'say \"hi\"'");
    }

    #[test]
    fn pretty_matches_python_indent2() {
        let value = py_object(vec![
            ("a".into(), json!(1)),
            ("list".into(), json!(["x", 2])),
        ]);
        assert_eq!(dumps_pretty(&value), "{\n  \"a\": 1,\n  \"list\": [\n    \"x\",\n    2\n  ]\n}\n");
    }
}

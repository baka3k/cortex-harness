//! Python `json.dumps(value, ensure_ascii=True, sort_keys=True)` — byte-compatible
//! serializer cho `[project_topology] {payload}` summary line (separators mặc
//! định `", "` / `": "`).

use std::collections::BTreeMap;

use serde_json::Value;

/// Serialize như Python json.dumps mặc định + sort_keys + ensure_ascii.
pub fn dumps_sorted(value: &Value) -> String {
    let mut out = String::new();
    write_value(value, &mut out);
    out
}

fn write_value(value: &Value, out: &mut String) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(number) => {
            // Python json in int nguyên dạng; float qua repr — summary chỉ
            // chứa int nên float nhánh giữ 1:1 với serde.
            out.push_str(&number.to_string());
        }
        Value::String(text) => write_escaped(text, out),
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                write_value(item, out);
            }
            out.push(']');
        }
        Value::Object(map) => {
            // sort_keys=True — BTreeMap sắp theo code point (khớp Python
            // sort trên str cho key ASCII/UTF-8 unit-difference: Python so
            // theo code point, Rust byte order của UTF-8 tương đương).
            let sorted: BTreeMap<&String, &Value> = map.iter().collect();
            out.push('{');
            for (index, (key, item)) in sorted.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                write_escaped(key, out);
                out.push_str(": ");
                write_value(item, out);
            }
            out.push('}');
        }
    }
}

/// `ensure_ascii=True` — non-ASCII escape \uXXXX (surrogate pair cho astral).
fn write_escaped(text: &str, out: &mut String) {
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c if (c as u32) <= 0x7f => out.push(c),
            c => {
                let code = c as u32;
                if code <= 0xffff {
                    out.push_str(&format!("\\u{code:04x}"));
                } else {
                    let reduced = code - 0x10000;
                    let high = 0xd800 + (reduced >> 10);
                    let low = 0xdc00 + (reduced & 0x3ff);
                    out.push_str(&format!("\\u{high:04x}\\u{low:04x}"));
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
    fn matches_python_default_separators_sorted() {
        let value = json!({"b": 1, "a": [true, null, "x"], "nested": {"z": "é", "y": 2}});
        assert_eq!(
            dumps_sorted(&value),
            "{\"a\": [true, null, \"x\"], \"b\": 1, \"nested\": {\"y\": 2, \"z\": \"\\u00e9\"}}"
        );
    }

    #[test]
    fn escapes_control_and_surrogates() {
        let value = json!("line\n\x01𝄞");
        assert_eq!(
            dumps_sorted(&value),
            "\"line\\n\\u0001\\ud834\\udd1e\""
        );
    }
}

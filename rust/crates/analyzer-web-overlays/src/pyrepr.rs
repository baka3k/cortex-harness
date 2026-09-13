//! Python `repr()` cho các giá trị nhỏ cần in stdout (dict summary của
//! `[overlay] ... graph={'nodes': 1, ...}` và f-string `semantic={bool}`).

use serde_json::Value;

/// `str(bool)` của Python — `"True"` / `"False"`.
pub fn py_bool(value: bool) -> &'static str {
    if value {
        "True"
    } else {
        "False"
    }
}

/// `repr(dict)` của Python cho dict có value scalar (str/int/float/bool/None),
/// giữ insertion order.
pub fn py_dict_repr(entries: &[(&str, Value)]) -> String {
    let inner = entries
        .iter()
        .map(|(key, value)| format!("{}: {}", py_str_repr(key), py_value_repr(value)))
        .collect::<Vec<_>>()
        .join(", ");
    format!("{{{inner}}}")
}

fn py_value_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(number) => {
            if let Some(int) = number.as_i64() {
                int.to_string()
            } else if let Some(uint) = number.as_u64() {
                uint.to_string()
            } else {
                let float = number.as_f64().unwrap_or_default();
                let text = format!("{float}");
                if text.contains('.') || text.contains('e') || text.contains('E') {
                    text
                } else {
                    format!("{text}.0")
                }
            }
        }
        Value::String(text) => py_str_repr(text),
        Value::Array(_) | Value::Object(_) => unreachable!("summary values are scalar"),
    }
}

/// `repr(str)` của Python: quote đơn trừ khi chuỗi chứa `'` mà không có `"`.
pub fn py_str_repr(text: &str) -> String {
    let has_single = text.contains('\'');
    let has_double = text.contains('"');
    let quote = if has_single && !has_double { '"' } else { '\'' };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for character in text.chars() {
        match character {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            control if (control as u32) < 0x20 => {
                out.push_str(&format!("\\x{:02x}", control as u32));
            }
            other => {
                if other as u32 == quote as u32 {
                    out.push('\\');
                }
                out.push(other);
            }
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn dict_repr_matches_python() {
        // repr({'stage': 'applied', 'nodes': 3, 'preserved_modules': 0})
        let output = py_dict_repr(&[
            ("stage", json!("applied")),
            ("nodes", json!(3)),
            ("preserved_modules", json!(0)),
        ]);
        assert_eq!(output, "{'stage': 'applied', 'nodes': 3, 'preserved_modules': 0}");
    }

    #[test]
    fn str_repr_quotes() {
        assert_eq!(py_str_repr("it's"), "\"it's\"");
        assert_eq!(py_str_repr("plain"), "'plain'");
    }
}

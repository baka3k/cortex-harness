//! Row model — JSON map cho dữ liệu writer (tương đương `dict` Python).
//!
//! Writer Python nhận `List[Dict[str, Any]]`; ở đây rows là
//! `Vec<serde_json::Map<String, Value>>`. Quy ước `row.<prop>` trên dict thiếu
//! key ≡ null — JSON map thiếu key được store diễn dịch y như vậy khi bind.

use serde_json::{Map, Value};

/// Alias kiểu một row (dict Python ↔ JSON map).
pub type Row = Map<String, Value>;

/// Đọc property của row: thiếu key → `Value::Null` (khớp `row.prop` trên dict
/// Python khi key vắng — thực chất là `KeyError` ở dict access trực tiếp, nhưng
/// mọi access trong Cypher map/param đều đi qua binding nên ≡ null).
pub fn row_get<'a>(row: &'a Row, key: &str) -> &'a Value {
    row.get(key).unwrap_or(&Value::Null)
}

/// Chuyển JSON value sang `f64` khi có thể (count/int so sánh).
pub fn as_i64(value: &Value) -> Option<i64> {
    match value {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Value::String(s) => s.parse().ok(),
        _ => None,
    }
}

/// setdefault("node_type", "code") của Python.
pub fn setdefault_code(row: &mut Row) {
    row.entry("node_type".to_string())
        .or_insert_with(|| Value::String("code".to_string()));
}

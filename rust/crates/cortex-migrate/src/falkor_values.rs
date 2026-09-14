//! Chuyển records FalkorDB (`id(n)`, `labels(n)`, `properties(n)`, `type(r)`,
//! `id(a)/id(b)`) thành [`NodeRow`]/[`RelRow`] cho writer, qua
//! [`cortex_falkordb::normalize::normalize_value`] — cùng tầng chuẩn hoá của
//! runtime/driver.

use serde_json::Value;

use cortex_falkordb::normalize::normalize_value;
use cortex_falkordb::value::FalkorValue;

use crate::ladybug_writer::{JsonMap, NodeRow, RelRow};

pub fn falkor_to_json(value: &FalkorValue) -> Value {
    normalize_value(value)
}

fn as_int(value: &FalkorValue, column: &str) -> Result<i64, String> {
    match value {
        FalkorValue::Int(int) => Ok(*int),
        other => Err(format!("column {column}: expected int, got {other:?}")),
    }
}

fn as_string(value: &FalkorValue, column: &str) -> Result<String, String> {
    match value {
        FalkorValue::String(text) => Ok(text.clone()),
        other => Err(format!("column {column}: expected string, got {other:?}")),
    }
}

fn as_strings(value: &FalkorValue, column: &str) -> Result<Vec<String>, String> {
    match value {
        FalkorValue::Array(items) => items
            .iter()
            .map(|item| as_string(item, column))
            .collect::<Result<Vec<_>, _>>(),
        FalkorValue::Null => Ok(Vec::new()),
        other => Err(format!("column {column}: expected string[], got {other:?}")),
    }
}

fn as_props(value: &FalkorValue, column: &str) -> Result<JsonMap, String> {
    match falkor_to_json(value) {
        Value::Object(map) => Ok(map),
        other => Err(format!(
            "column {column}: expected properties map, got {other}"
        )),
    }
}

/// Row của query
/// `RETURN id(n) AS _fid, labels(n) AS _labels, properties(n) AS _props`.
pub fn parse_node_row(record: &[FalkorValue]) -> Result<NodeRow, String> {
    if record.len() < 3 {
        return Err(format!("node record needs 3 columns, got {}", record.len()));
    }
    Ok(NodeRow {
        fid: as_int(&record[0], "_fid")?,
        labels: as_strings(&record[1], "_labels")?,
        props: as_props(&record[2], "_props")?,
    })
}

/// Row của query `RETURN id(r) AS _fid, type(r) AS _type, id(a) AS _src,
/// id(b) AS _dst, labels(a) AS _sl, labels(b) AS _dl, properties(r) AS _props`.
pub fn parse_rel_row(record: &[FalkorValue]) -> Result<RelRow, String> {
    if record.len() < 7 {
        return Err(format!("rel record needs 7 columns, got {}", record.len()));
    }
    Ok(RelRow {
        fid: as_int(&record[0], "_fid")?,
        src_fid: as_int(&record[2], "_src")?,
        dst_fid: as_int(&record[3], "_dst")?,
        endpoints: (
            primary_label(&as_strings(&record[4], "_sl")?),
            primary_label(&as_strings(&record[5], "_dl")?),
        ),
        props: as_props(&record[6], "_props")?,
    })
}

fn as_rel_type(value: &FalkorValue) -> Result<String, String> {
    as_string(value, "_type")
}

/// Primary label: phần tử đầu của `labels(n)`; không label → `_unlabeled`.
pub fn primary_label(labels: &[String]) -> String {
    labels
        .first()
        .cloned()
        .unwrap_or_else(|| "_unlabeled".to_string())
}

/// Ghép type(r) vào row (parse riêng để caller xử lý grouping theo type).
pub fn rel_type_of(record: &[FalkorValue]) -> Result<String, String> {
    record
        .get(1)
        .map(as_rel_type)
        .unwrap_or_else(|| Err("rel record missing _type".to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_node_row() {
        let record = vec![
            FalkorValue::Int(5),
            FalkorValue::Array(vec![
                FalkorValue::String("Person".into()),
                FalkorValue::String("Employee".into()),
            ]),
            FalkorValue::Map(vec![
                ("name".to_string(), FalkorValue::String("Bob".into())),
                ("age".to_string(), FalkorValue::Int(30)),
            ]),
        ];
        let row = parse_node_row(&record).unwrap();
        assert_eq!(row.fid, 5);
        assert_eq!(row.labels, vec!["Person", "Employee"]);
        assert_eq!(row.props.get("name").and_then(Value::as_str), Some("Bob"));
    }

    #[test]
    fn parses_rel_row_and_type() {
        let record = vec![
            FalkorValue::Int(9),
            FalkorValue::String("KNOWS".into()),
            FalkorValue::Int(1),
            FalkorValue::Int(2),
            FalkorValue::Array(vec![FalkorValue::String("Person".into())]),
            FalkorValue::Array(vec![]),
            FalkorValue::Map(vec![("since".to_string(), FalkorValue::Int(2020))]),
        ];
        assert_eq!(rel_type_of(&record).unwrap(), "KNOWS");
        let row = parse_rel_row(&record).unwrap();
        assert_eq!(row.endpoints, ("Person".to_string(), "_unlabeled".to_string()));
        assert_eq!(row.src_fid, 1);
    }
}

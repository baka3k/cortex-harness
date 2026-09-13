//! Chuẩn hoá [`FalkorValue`] thành JSON khớp byte-level với
//! `tools/graph/driver/falkordb_driver.py::_normalize_falkordb_value` —
//! tầng mà parity harness và (sau Phase 03) `GraphStore` so sánh.
//!
//! Quy tắc của bản Python:
//! - Node → dict(props); `setdefault("_graph_id", node.id)`;
//!   `setdefault("_label", sorted(labels)[0])` (bỏ qua khi không có label).
//! - Edge → dict(props); setdefault `_type` (relation), `_start_id`, `_end_id`.
//! - Path → `{"nodes": [...], "edges": [...]}`.
//! - Map/list → đệ quy. Scalar → nguyên trạng.

use serde_json::{Map, Number, Value};

use crate::value::FalkorValue;

fn setdefault(node_obj: &mut Map<String, Value>, key: &str, value: Value) {
    node_obj.entry(key.to_string()).or_insert(value);
}

pub fn normalize_value(value: &FalkorValue) -> Value {
    match value {
        FalkorValue::Null => Value::Null,
        FalkorValue::Bool(b) => Value::Bool(*b),
        FalkorValue::Int(i) => Value::Number(Number::from(*i)),
        FalkorValue::Double(d) => Number::from_f64(*d)
            .map(Value::Number)
            .unwrap_or(Value::Null),
        FalkorValue::String(s) => Value::String(s.clone()),
        FalkorValue::Array(items) => Value::Array(items.iter().map(normalize_value).collect()),
        FalkorValue::Node {
            id,
            labels,
            properties,
        } => {
            let mut obj = properties_to_json(properties);
            setdefault(&mut obj, "_graph_id", Value::Number(Number::from(*id)));
            if let Some(first_label) = sorted_first_label(labels) {
                setdefault(&mut obj, "_label", Value::String(first_label));
            }
            Value::Object(obj)
        }
        FalkorValue::Edge {
            relation,
            src_id,
            dest_id,
            properties,
            ..
        } => {
            let mut obj = properties_to_json(properties);
            setdefault(&mut obj, "_type", Value::String(relation.clone()));
            setdefault(
                &mut obj,
                "_start_id",
                Value::Number(Number::from(*src_id)),
            );
            setdefault(&mut obj, "_end_id", Value::Number(Number::from(*dest_id)));
            Value::Object(obj)
        }
        FalkorValue::Path { nodes, edges } => {
            let mut obj = Map::new();
            obj.insert(
                "nodes".into(),
                Value::Array(nodes.iter().map(normalize_value).collect()),
            );
            obj.insert(
                "edges".into(),
                Value::Array(edges.iter().map(normalize_value).collect()),
            );
            Value::Object(obj)
        }
        FalkorValue::Map(entries) => {
            let mut obj = Map::new();
            for (key, value) in entries {
                obj.insert(key.clone(), normalize_value(value));
            }
            Value::Object(obj)
        }
        FalkorValue::Point {
            latitude,
            longitude,
        } => {
            let mut obj = Map::new();
            obj.insert("latitude".into(), Number::from_f64(*latitude).into());
            obj.insert("longitude".into(), Number::from_f64(*longitude).into());
            Value::Object(obj)
        }
    }
}

fn properties_to_json(properties: &std::collections::BTreeMap<String, FalkorValue>) -> Map<String, Value> {
    let mut obj = Map::new();
    for (key, value) in properties {
        obj.insert(key.clone(), normalize_value(value));
    }
    obj
}

/// `sorted(str(label) for label in labels)[0]` — sort lexical, lấy phần tử đầu.
fn sorted_first_label(labels: &[String]) -> Option<String> {
    if labels.is_empty() {
        return None;
    }
    let mut sorted: Vec<&str> = labels.iter().map(String::as_str).collect();
    sorted.sort();
    sorted.into_iter().next().map(str::to_string)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    fn node(id: i64, labels: Vec<&str>, props: &[(&str, FalkorValue)]) -> FalkorValue {
        FalkorValue::Node {
            id,
            labels: labels.into_iter().map(String::from).collect(),
            properties: props
                .iter()
                .map(|(k, v)| (k.to_string(), v.clone()))
                .collect::<BTreeMap<String, FalkorValue>>(),
        }
    }

    #[test]
    fn node_gets_graph_id_and_sorted_first_label() {
        let value = node(42, vec!["Function", "Callable"], &[("name", FalkorValue::String("f".into()))]);
        let json = normalize_value(&value);
        let obj = json.as_object().unwrap();
        assert_eq!(obj.get("_graph_id"), Some(&Value::Number(42.into())));
        // sorted(["Function", "Callable"])[0] == "Callable"
        assert_eq!(obj.get("_label"), Some(&Value::String("Callable".into())));
        assert_eq!(obj.get("name"), Some(&Value::String("f".into())));
    }

    #[test]
    fn existing_meta_properties_win_over_setdefault() {
        let value = node(
            42,
            vec!["A", "B"],
            &[("_graph_id", FalkorValue::Int(999)), ("_label", FalkorValue::String("Custom".into()))],
        );
        let obj = normalize_value(&value).as_object().unwrap().clone();
        assert_eq!(obj.get("_graph_id"), Some(&Value::Number(999.into())));
        assert_eq!(obj.get("_label"), Some(&Value::String("Custom".into())));
    }

    #[test]
    fn edge_metadata_fields() {
        let edge = FalkorValue::Edge {
            id: 7,
            relation: "CALLS".into(),
            src_id: 1,
            dest_id: 2,
            properties: BTreeMap::new(),
        };
        let obj = normalize_value(&edge).as_object().unwrap().clone();
        assert_eq!(obj.get("_type"), Some(&Value::String("CALLS".into())));
        assert_eq!(obj.get("_start_id"), Some(&Value::Number(1.into())));
        assert_eq!(obj.get("_end_id"), Some(&Value::Number(2.into())));
    }
}

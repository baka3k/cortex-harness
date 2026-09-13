//! Compact result-set value model của FalkorDB/Redis graph engine.
//!
//! Mọi record value đi trên dây dưới dạng `[type, payload]`. Bảng type khớp
//! `ResultSetScalarTypes` của falkordb-py (xem `falkordb/query_result.py`):
//! 1=NULL, 2=STRING, 3=INTEGER, 4=BOOLEAN, 5=DOUBLE, 6=ARRAY, 7=EDGE,
//! 8=NODE, 9=PATH, 10=MAP, 11=POINT.
//!
//! Payload đặc biệt:
//! - BOOLEAN/DOUBLE: payload là *chuỗi* ("true"/"false", "1.5").
//! - NODE: `[id, [label_ids], [prop_triples]]` với prop_triple =
//!   `[prop_id, value_type, value]`; label_ids tra `GraphSchema`.
//! - EDGE: `[id, rel_type_id, src_id, dest_id, [prop_triples]]`.
//! - PATH: `[node_value, edge_value]` (mỗi phần tử là một value `[6, [...]]`).
//! - MAP: mảng phẳng `[k1, v1, k2, v2, ...]`, value vẫn là `[type, payload]`.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::schema::{GraphSchema, SchemaError};

pub const VALUE_NULL: i64 = 1;
pub const VALUE_STRING: i64 = 2;
pub const VALUE_INTEGER: i64 = 3;
pub const VALUE_BOOLEAN: i64 = 4;
pub const VALUE_DOUBLE: i64 = 5;
pub const VALUE_ARRAY: i64 = 6;
pub const VALUE_EDGE: i64 = 7;
pub const VALUE_NODE: i64 = 8;
pub const VALUE_PATH: i64 = 9;
pub const VALUE_MAP: i64 = 10;
pub const VALUE_POINT: i64 = 11;

/// Một giá trị đã parse từ result set compact.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum FalkorValue {
    Null,
    Bool(bool),
    Int(i64),
    Double(f64),
    String(String),
    Array(Vec<FalkorValue>),
    Node {
        id: i64,
        labels: Vec<String>,
        properties: BTreeMap<String, FalkorValue>,
    },
    Edge {
        id: i64,
        relation: String,
        src_id: i64,
        dest_id: i64,
        properties: BTreeMap<String, FalkorValue>,
    },
    Path {
        nodes: Vec<FalkorValue>,
        edges: Vec<FalkorValue>,
    },
    Map(Vec<(String, FalkorValue)>),
    Point {
        latitude: f64,
        longitude: f64,
    },
}

#[derive(Debug)]
pub enum ParseError {
    Redis(redis::RedisError),
    Schema(SchemaError),
    /// Cấu trúc reply không đúng giao thức.
    Malformed(String),
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ParseError::Redis(e) => write!(f, "redis: {e}"),
            ParseError::Schema(e) => write!(f, "schema: {e}"),
            ParseError::Malformed(m) => write!(f, "malformed reply: {m}"),
        }
    }
}

impl std::error::Error for ParseError {}

impl From<redis::RedisError> for ParseError {
    fn from(e: redis::RedisError) -> Self {
        ParseError::Redis(e)
    }
}

impl From<SchemaError> for ParseError {
    fn from(e: SchemaError) -> Self {
        ParseError::Schema(e)
    }
}

fn as_array<'a>(v: &'a redis::Value, what: &str) -> Result<&'a Vec<redis::Value>, ParseError> {
    match v {
        redis::Value::Array(items) => Ok(items),
        other => Err(ParseError::Malformed(format!(
            "{what}: expected array, got {other:?}"
        ))),
    }
}

fn as_int(v: &redis::Value, what: &str) -> Result<i64, ParseError> {
    match v {
        redis::Value::Int(i) => Ok(*i),
        redis::Value::BulkString(bytes) => std::str::from_utf8(bytes)
            .ok()
            .and_then(|s| s.parse().ok())
            .ok_or_else(|| ParseError::Malformed(format!("{what}: bad int {bytes:?}"))),
        other => Err(ParseError::Malformed(format!(
            "{what}: expected int, got {other:?}"
        ))),
    }
}

fn as_string(v: &redis::Value, what: &str) -> Result<String, ParseError> {
    match v {
        redis::Value::BulkString(bytes) => String::from_utf8(bytes.clone())
            .map_err(|e| ParseError::Malformed(format!("{what}: bad utf-8: {e}"))),
        redis::Value::SimpleString(s) => Ok(s.clone()),
        other => Err(ParseError::Malformed(format!(
            "{what}: expected string, got {other:?}"
        ))),
    }
}

/// Parse một value `[type, payload]` khỏi result set compact.
///
/// Khi gặp label/rel-type/property id nằm ngoài bảng tên hiện có, trả về
/// [`ParseError::Schema`] để caller refresh schema rồi parse lại (giống hành vi
/// refresh-on-mismatch của falkordb-py).
pub fn parse_value(raw: &redis::Value, schema: &mut GraphSchema) -> Result<FalkorValue, ParseError> {
    let pair = as_array(raw, "value pair")?;
    if pair.len() != 2 {
        return Err(ParseError::Malformed(format!(
            "value pair phải có 2 phần tử, got {}",
            pair.len()
        )));
    }
    let kind = as_int(&pair[0], "value type")?;
    let payload = &pair[1];
    parse_payload(kind, payload, schema)
}

fn parse_payload(
    kind: i64,
    payload: &redis::Value,
    schema: &mut GraphSchema,
) -> Result<FalkorValue, ParseError> {
    match kind {
        VALUE_NULL => Ok(FalkorValue::Null),
        VALUE_STRING => Ok(FalkorValue::String(as_string(payload, "string")?)),
        VALUE_INTEGER => Ok(FalkorValue::Int(as_int(payload, "integer")?)),
        VALUE_BOOLEAN => {
            let text = as_string(payload, "boolean")?;
            match text.as_str() {
                "true" => Ok(FalkorValue::Bool(true)),
                "false" => Ok(FalkorValue::Bool(false)),
                other => Err(ParseError::Malformed(format!("bad boolean {other:?}"))),
            }
        }
        VALUE_DOUBLE => {
            let text = as_string(payload, "double")?;
            text.parse::<f64>()
                .map(FalkorValue::Double)
                .map_err(|e| ParseError::Malformed(format!("bad double {text:?}: {e}")))
        }
        VALUE_ARRAY => {
            let items = as_array(payload, "array")?;
            let mut out = Vec::with_capacity(items.len());
            for item in items {
                out.push(parse_value(item, schema)?);
            }
            Ok(FalkorValue::Array(out))
        }
        VALUE_EDGE => parse_edge(payload, schema),
        VALUE_NODE => parse_node(payload, schema),
        VALUE_PATH => {
            let parts = as_array(payload, "path")?;
            if parts.len() != 2 {
                return Err(ParseError::Malformed("path cần đúng 2 phần tử".into()));
            }
            let nodes_value = parse_value(&parts[0], schema)?;
            let edges_value = parse_value(&parts[1], schema)?;
            let nodes = match nodes_value {
                FalkorValue::Array(nodes) => nodes,
                other => return Err(ParseError::Malformed(format!("path.nodes: {other:?}"))),
            };
            let edges = match edges_value {
                FalkorValue::Array(edges) => edges,
                other => return Err(ParseError::Malformed(format!("path.edges: {other:?}"))),
            };
            Ok(FalkorValue::Path { nodes, edges })
        }
        VALUE_MAP => {
            let items = as_array(payload, "map")?;
            if items.len() % 2 != 0 {
                return Err(ParseError::Malformed("map phải có số phần tử chẵn".into()));
            }
            let mut entries = Vec::with_capacity(items.len() / 2);
            for pair in items.chunks(2) {
                let key = as_string(&pair[0], "map key")?;
                let value = parse_value(&pair[1], schema)?;
                entries.push((key, value));
            }
            Ok(FalkorValue::Map(entries))
        }
        VALUE_POINT => {
            let coords = as_array(payload, "point")?;
            if coords.len() != 2 {
                return Err(ParseError::Malformed("point cần 2 toạ độ".into()));
            }
            let latitude = match &coords[0] {
                redis::Value::Double(d) => *d,
                redis::Value::BulkString(b) => std::str::from_utf8(b)
                    .ok()
                    .and_then(|s| s.parse().ok())
                    .ok_or_else(|| ParseError::Malformed("bad point latitude".into()))?,
                redis::Value::Int(i) => *i as f64,
                other => {
                    return Err(ParseError::Malformed(format!("point lat: {other:?}")));
                }
            };
            let longitude = match &coords[1] {
                redis::Value::Double(d) => *d,
                redis::Value::BulkString(b) => std::str::from_utf8(b)
                    .ok()
                    .and_then(|s| s.parse().ok())
                    .ok_or_else(|| ParseError::Malformed("bad point longitude".into()))?,
                redis::Value::Int(i) => *i as f64,
                other => {
                    return Err(ParseError::Malformed(format!("point lon: {other:?}")));
                }
            };
            Ok(FalkorValue::Point {
                latitude,
                longitude,
            })
        }
        other => Err(ParseError::Malformed(format!(
            "value type {other} chưa hỗ trợ"
        ))),
    }
}

/// Parse property triple `[prop_id, value_type, value]`.
fn parse_property(
    raw: &redis::Value,
    schema: &mut GraphSchema,
) -> Result<(String, FalkorValue), ParseError> {
    let triple = as_array(raw, "property triple")?;
    if triple.len() != 3 {
        return Err(ParseError::Malformed(format!(
            "property triple cần 3 phần tử, got {}",
            triple.len()
        )));
    }
    let prop_id = as_int(&triple[0], "property id")?;
    let name = schema.property_name(prop_id)?;
    let value = parse_payload(as_int(&triple[1], "property value type")?, &triple[2], schema)?;
    Ok((name, value))
}

fn parse_node(payload: &redis::Value, schema: &mut GraphSchema) -> Result<FalkorValue, ParseError> {
    let parts = as_array(payload, "node")?;
    if parts.len() != 3 {
        return Err(ParseError::Malformed(format!(
            "node cần [id, labels, props], got {} phần tử",
            parts.len()
        )));
    }
    let id = as_int(&parts[0], "node id")?;
    let mut labels = Vec::new();
    for label_raw in as_array(&parts[1], "node labels")? {
        let label_id = as_int(label_raw, "label id")?;
        labels.push(schema.label_name(label_id)?);
    }
    let mut properties = BTreeMap::new();
    for prop_raw in as_array(&parts[2], "node properties")? {
        let (name, value) = parse_property(prop_raw, schema)?;
        properties.insert(name, value);
    }
    Ok(FalkorValue::Node {
        id,
        labels,
        properties,
    })
}

fn parse_edge(payload: &redis::Value, schema: &mut GraphSchema) -> Result<FalkorValue, ParseError> {
    let parts = as_array(payload, "edge")?;
    if parts.len() != 5 {
        return Err(ParseError::Malformed(format!(
            "edge cần [id, rel_type_id, src, dest, props], got {} phần tử",
            parts.len()
        )));
    }
    let id = as_int(&parts[0], "edge id")?;
    let relation_id = as_int(&parts[1], "edge rel type id")?;
    let relation = schema.relationship_name(relation_id)?;
    let src_id = as_int(&parts[2], "edge src id")?;
    let dest_id = as_int(&parts[3], "edge dest id")?;
    let mut properties = BTreeMap::new();
    for prop_raw in as_array(&parts[4], "edge properties")? {
        let (name, value) = parse_property(prop_raw, schema)?;
        properties.insert(name, value);
    }
    Ok(FalkorValue::Edge {
        id,
        relation,
        src_id,
        dest_id,
        properties,
    })
}

impl FalkorValue {
    /// Chuyển sang JSON — shape khớp `_normalize_falkordb_value` của
    /// `falkordb_driver.py`: node → `{props..., _label, _graph_id}`,
    /// edge → `{props..., _type, _src, _dst}`, path → `{nodes, edges}`.
    pub fn to_json_value(&self) -> serde_json::Value {
        use serde_json::{json, Map, Value as Json};
        match self {
            FalkorValue::Null => Json::Null,
            FalkorValue::Bool(b) => Json::Bool(*b),
            FalkorValue::Int(i) => json!(i),
            FalkorValue::Double(d) => json!(d),
            FalkorValue::String(s) => Json::String(s.clone()),
            FalkorValue::Array(items) => {
                Json::Array(items.iter().map(Self::to_json_value).collect())
            }
            FalkorValue::Map(entries) => {
                let mut map = Map::new();
                for (key, value) in entries {
                    map.insert(key.clone(), value.to_json_value());
                }
                Json::Object(map)
            }
            FalkorValue::Node {
                id,
                labels,
                properties,
            } => {
                let mut map = Map::new();
                for (key, value) in properties {
                    map.insert(key.clone(), value.to_json_value());
                }
                if let Some(label) = labels.iter().min() {
                    map.insert("_label".to_string(), Json::String(label.clone()));
                }
                map.insert("_graph_id".to_string(), json!(id));
                Json::Object(map)
            }
            FalkorValue::Edge {
                id,
                relation,
                src_id,
                dest_id,
                properties,
            } => {
                let mut map = Map::new();
                for (key, value) in properties {
                    map.insert(key.clone(), value.to_json_value());
                }
                map.insert("_type".to_string(), Json::String(relation.clone()));
                map.insert("_edge_id".to_string(), json!(id));
                map.insert("_src".to_string(), json!(src_id));
                map.insert("_dst".to_string(), json!(dest_id));
                Json::Object(map)
            }
            FalkorValue::Path { nodes, edges } => json!({
                "nodes": nodes.iter().map(Self::to_json_value).collect::<Vec<_>>(),
                "edges": edges.iter().map(Self::to_json_value).collect::<Vec<_>>(),
            }),
            FalkorValue::Point {
                latitude,
                longitude,
            } => json!({"latitude": latitude, "longitude": longitude}),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn schema() -> GraphSchema {
        GraphSchema::from_tables(
            vec!["Project".into(), "Function".into()],
            vec!["CONTAINS".into()],
            vec!["name".into(), "repo".into()],
        )
    }

    fn s(bytes: &[u8]) -> redis::Value {
        redis::Value::BulkString(bytes.to_vec())
    }

    #[test]
    fn scalar_encodings() {
        let mut sc = schema();
        // [2, "hello"] -> String
        assert_eq!(
            parse_value(
                &redis::Value::Array(vec![redis::Value::Int(2), s(b"hello")]),
                &mut sc
            )
            .unwrap(),
            FalkorValue::String("hello".into())
        );
        // [3, 42] -> Int
        assert_eq!(
            parse_value(&redis::Value::Array(vec![redis::Value::Int(3), redis::Value::Int(42)]), &mut sc)
                .unwrap(),
            FalkorValue::Int(42)
        );
        // [4, "true"] -> Bool (payload là chuỗi)
        assert_eq!(
            parse_value(&redis::Value::Array(vec![redis::Value::Int(4), s(b"true")]), &mut sc).unwrap(),
            FalkorValue::Bool(true)
        );
        // [5, "1.5"] -> Double (payload là chuỗi)
        assert_eq!(
            parse_value(&redis::Value::Array(vec![redis::Value::Int(5), s(b"1.5")]), &mut sc).unwrap(),
            FalkorValue::Double(1.5)
        );
        // [1, nil] -> Null
        assert_eq!(
            parse_value(
                &redis::Value::Array(vec![redis::Value::Int(1), redis::Value::Nil]),
                &mut sc
            )
            .unwrap(),
            FalkorValue::Null
        );
    }

    #[test]
    fn node_encoding_with_schema_ids() {
        let mut sc = schema();
        // Node: [99, [1], [[0, 2, "fn"], [1, 3, 7]]] -> Function{name: "fn", repo: 7}
        let node = redis::Value::Array(vec![
            redis::Value::Int(99),
            redis::Value::Array(vec![redis::Value::Int(1)]),
            redis::Value::Array(vec![
                redis::Value::Array(vec![redis::Value::Int(0), redis::Value::Int(2), s(b"fn")]),
                redis::Value::Array(vec![
                    redis::Value::Int(1),
                    redis::Value::Int(3),
                    redis::Value::Int(7),
                ]),
            ]),
        ]);
        let pair = redis::Value::Array(vec![redis::Value::Int(8), node]);
        match parse_value(&pair, &mut sc).unwrap() {
            FalkorValue::Node {
                id,
                labels,
                properties,
            } => {
                assert_eq!(id, 99);
                assert_eq!(labels, vec!["Function".to_string()]);
                assert_eq!(properties.get("name"), Some(&FalkorValue::String("fn".into())));
                assert_eq!(properties.get("repo"), Some(&FalkorValue::Int(7)));
            }
            other => panic!("expected node, got {other:?}"),
        }
    }

    #[test]
    fn unknown_label_id_signals_schema_refresh() {
        let mut sc = schema();
        let node = redis::Value::Array(vec![
            redis::Value::Int(1),
            redis::Value::Array(vec![redis::Value::Int(77)]),
            redis::Value::Array(vec![]),
        ]);
        let pair = redis::Value::Array(vec![redis::Value::Int(8), node]);
        assert!(matches!(
            parse_value(&pair, &mut sc),
            Err(ParseError::Schema(SchemaError::OutOfRange { .. }))
        ));
    }
}

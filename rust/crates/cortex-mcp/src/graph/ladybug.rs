//! Embedded LadybugDB store client for the graph runtime (ladybug runtime
//! port — the follow-up of `plans/260915-2027-vector-lane-rust-port`).
//!
//! Python's `LadybugDriver` model, mirrored here:
//! * one named graph = one store FILE (`<owner>.lbug/<graph-name>`), opened
//!   in-process through the `lbug` crate (same engine + store format as the
//!   PyPI `ladybug` package the Python side uses);
//! * the primary store path comes from `LADYBUG_{CODE,DOC}_PATH` / the
//!   storage layout; sibling graphs are store files beside it;
//! * queries are plain Cypher with `$param` bindings.
//!
//! The falkordb `Param` enum is reused as the parameter surface so the
//! runtime/`DocGraphStore` call sites stay backend-agnostic. Row values are
//! normalized to the same JSON shapes as `cortex_falkordb::normalize`
//! (node → props + `_graph_id` + `_label`, edge → props + `_type`).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use cortex_falkordb::client::Param;
use lbug::{Connection, Database, LogicalType, SystemConfig, Value};
use serde_json::{json, Map, Value as Json};

/// An opened store. `Connection<'a>` borrows its `Database`, so the database
/// is intentionally leaked into `'static` — server stores live for the whole
/// process and `Database`/`Connection` are both `Send + Sync`.
pub struct LadybugStore {
    connection: Connection<'static>,
    read_only: bool,
}

impl LadybugStore {
    pub fn open(path: &Path, read_only: bool) -> Result<Self, String> {
        let config = SystemConfig::default().read_only(read_only);
        let database = Database::new(path, config).map_err(|error| {
            format!("cannot open ladybug store {}: {error}", path.display())
        })?;
        let database: &'static Database = Box::leak(Box::new(database));
        let connection =
            Connection::new(database).map_err(|error| format!("ladybug connect: {error}"))?;
        Ok(Self { connection, read_only })
    }

    pub fn is_read_only(&self) -> bool {
        self.read_only
    }

    /// Run Cypher with falkordb-style params → row maps (column name →
    /// normalized JSON value).
    pub fn query(
        &self,
        cypher: &str,
        params: &BTreeMap<String, Param>,
    ) -> Result<Vec<Map<String, Json>>, String> {
        // `lbug::Error::FailedQuery` hiển thị "Query execution failed: …" —
        // python nổi lỗi gốc không prefix, strip để error text khớp contract.
        let fail = |error: lbug::Error| -> String {
            error
                .to_string()
                .trim_start_matches("Query execution failed: ")
                .to_string()
        };
        let result = if params.is_empty() {
            self.connection.query(cypher).map_err(fail)?
        } else {
            let mut prepared = self.connection.prepare(cypher).map_err(fail)?;
            let mut bound = Vec::with_capacity(params.len());
            for (key, param) in params {
                let value =
                    param_to_lbug(param).map_err(|error| format!("param ${key}: {error}"))?;
                bound.push((key.as_str(), value));
            }
            self.connection.execute(&mut prepared, bound).map_err(fail)?
        };
        rows_to_maps(result)
    }
}

fn rows_to_maps(result: lbug::QueryResult) -> Result<Vec<Map<String, Json>>, String> {
    let names = result.get_column_names();
    let mut rows = Vec::new();
    for row in result {
        let mut map = Map::new();
        for (index, value) in row.iter().enumerate() {
            let key =
                names.get(index).cloned().unwrap_or_else(|| format!("column_{index}"));
            map.insert(key, value_to_json(value));
        }
        rows.push(map);
    }
    Ok(rows)
}

fn param_to_lbug(param: &Param) -> Result<Value, String> {
    Ok(match param {
        Param::Null => Value::Null(LogicalType::Any),
        Param::Bool(value) => Value::Bool(*value),
        Param::Int(value) => Value::Int64(*value),
        Param::Float(value) => Value::Double(*value),
        Param::Str(value) => Value::String(value.clone()),
        Param::List(items) => {
            let child = match items.first() {
                Some(item) => param_list_child(item),
                None => LogicalType::String,
            };
            let converted = items
                .iter()
                .map(param_to_lbug)
                .collect::<Result<Vec<_>, String>>()?;
            Value::List(child, converted)
        }
        Param::Map(pairs) => {
            // lbug infers struct field types from the values themselves.
            let converted = pairs
                .iter()
                .map(|(key, param)| Ok((key.clone(), param_to_lbug(param)?)))
                .collect::<Result<Vec<_>, String>>()?;
            Value::Struct(converted)
        }
    })
}

fn param_list_child(param: &Param) -> LogicalType {
    match param {
        Param::Bool(_) => LogicalType::Bool,
        Param::Int(_) => LogicalType::Int64,
        Param::Float(_) => LogicalType::Double,
        Param::Str(_) => LogicalType::String,
        Param::List(items) => items
            .first()
            .map(param_list_child)
            .unwrap_or(LogicalType::String),
        _ => LogicalType::Any,
    }
}

/// `cortex_falkordb::normalize` shape parity.
fn value_to_json(value: &Value) -> Json {
    match value {
        Value::Null(_) => Json::Null,
        Value::Bool(inner) => json!(inner),
        Value::Int64(inner) => json!(inner),
        Value::Int32(inner) => json!(inner),
        Value::Int16(inner) => json!(inner),
        Value::Int8(inner) => json!(inner),
        Value::UInt64(inner) => json!(inner),
        Value::UInt32(inner) => json!(inner),
        Value::UInt16(inner) => json!(inner),
        Value::UInt8(inner) => json!(inner),
        Value::Int128(inner) => json!(*inner as i64),
        Value::Double(inner) => json!(inner),
        Value::Float(inner) => json!(inner),
        Value::Date(inner) => json!(inner.to_string()),
        Value::Timestamp(inner) => json!(inner.to_string()),
        Value::TimestampTz(inner) => json!(inner.to_string()),
        Value::TimestampNs(inner) => json!(inner.to_string()),
        Value::TimestampMs(inner) => json!(inner.to_string()),
        Value::TimestampSec(inner) => json!(inner.to_string()),
        Value::Interval(inner) => json!(format!("{inner:?}")),
        Value::String(inner) => json!(inner),
        Value::Blob(inner) => json!(format!("{inner:?}")),
        Value::List(_, items) | Value::Array(_, items) => {
            Json::Array(items.iter().map(value_to_json).collect())
        }
        Value::Struct(fields) => {
            let mut object = Map::new();
            for (key, item) in fields {
                object.insert(key.clone(), value_to_json(item));
            }
            Json::Object(object)
        }
        Value::Map(_, pairs) => {
            let mut object = Map::new();
            for (key, item) in pairs {
                object.insert(key.to_string(), value_to_json(item));
            }
            Json::Object(object)
        }
        Value::Node(node) => {
            let mut object = Map::new();
            for (key, item) in node.get_properties() {
                object.insert(key.clone(), value_to_json(item));
            }
            object
                .entry("_graph_id".to_string())
                .or_insert_with(|| json!(node.get_node_id().offset));
            object
                .entry("_label".to_string())
                .or_insert_with(|| json!(node.get_label_name()));
            Json::Object(object)
        }
        Value::Rel(rel) => {
            let mut object = Map::new();
            for (key, item) in rel.get_properties() {
                object.insert(key.clone(), value_to_json(item));
            }
            object
                .entry("_type".to_string())
                .or_insert_with(|| json!(rel.get_label_name()));
            Json::Object(object)
        }
        _ => Json::Null,
    }
}

// ---------------------------------------------------------------------------
// Store discovery
// ---------------------------------------------------------------------------

/// Primary store file for a lane: `LADYBUG_<ROLE>_PATH` (`LADYBUG_CODE_PATH`
/// / `LADYBUG_DOC_PATH`) then the shared `LADYBUG_PATH`.
pub fn env_primary_store(role: &str) -> Option<PathBuf> {
    let mut keys = vec![format!("LADYBUG_{}_PATH", role.to_uppercase())];
    if role == "code" {
        // Legacy alias seen in older env files.
        keys.push("LADYBUG_PATH".to_string());
    }
    for key in keys {
        if let Ok(path) = std::env::var(&key) {
            let path = PathBuf::from(path.trim());
            if !path.as_os_str().is_empty() {
                return Some(path);
            }
        }
    }
    None
}

/// Sibling store files for a primary store (`_open_additional_stores`): every
/// other FILE in the same `<owner>.lbug` directory is a named graph.
pub fn sibling_store_files(primary: &Path) -> Vec<PathBuf> {
    let mut siblings = Vec::new();
    let Some(dir) = primary.parent() else {
        return siblings;
    };
    let Ok(entries) = std::fs::read_dir(dir) else {
        return siblings;
    };
    let mut files: Vec<PathBuf> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.is_file())
        .collect();
    files.sort();
    for file in files {
        if file != primary {
            siblings.push(file);
        }
    }
    siblings
}

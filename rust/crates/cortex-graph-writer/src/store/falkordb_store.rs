//! FalkorDB backend cho [`GraphStore`] — bọc `cortex_falkordb::FalkorDbClient`,
//! tái tạo đường chuẩn bị query của `falkordb_driver.py`:
//!
//! 1. `normalize_call_importing_subqueries`;
//! 2. `prepare_project_scope_parameters` (thêm sibling `*_normalized`);
//! 3. `rewrite_datetime_call` với `${param}` (FalkorDB nhận ISO-8601 string
//!    trực tiếp) — param `__falkordb_now`.
//!
//! Params đi qua CYPHER header kiểu falkordb-py — dict → `{`k`:v,...}`
//! (xem `Param::Map`). Records parse về JSON theo `_normalize_falkordb_value`.

use std::collections::BTreeMap;

use cortex_falkordb::client::{ClientError, FalkorDbClient, Param};
use serde_json::Value;

use crate::json_row::Row;
use crate::project_scope::prepare_project_scope_parameters;
use crate::query_normalize::{normalize_call_importing_subqueries, rewrite_datetime_call};
use crate::store::{GraphStore, IndexSpec, QueryRecords, StoreError};

pub struct FalkorDbStore {
    client: FalkorDbClient,
    graph: String,
    schema_ready: bool,
}

impl FalkorDbStore {
    pub fn new(client: FalkorDbClient, graph: impl Into<String>) -> Self {
        Self {
            client,
            graph: graph.into(),
            schema_ready: false,
        }
    }

    fn graph_for<'a>(&'a self, database: Option<&'a str>) -> &'a str {
        database.unwrap_or(&self.graph)
    }

    /// JSON value → `Param` (đệ quy). Float format khớp `str()` của Python
    /// cho trường hợp thường gặp (2.0 → "2.0" chứ không "2").
    pub fn value_to_param(value: &Value) -> Result<Param, StoreError> {
        Ok(match value {
            Value::Null => Param::Null,
            Value::Bool(b) => Param::Bool(*b),
            Value::Number(n) => {
                if let Some(i) = n.as_i64() {
                    Param::Int(i)
                } else {
                    Param::Float(n.as_f64().unwrap_or_default())
                }
            }
            Value::String(s) => Param::Str(s.clone()),
            Value::Array(items) => {
                let mut list = Vec::with_capacity(items.len());
                for item in items {
                    list.push(Self::value_to_param(item)?);
                }
                Param::List(list)
            }
            Value::Object(map) => {
                let mut entries = Vec::with_capacity(map.len());
                for (key, item) in map {
                    cortex_falkordb::client::validate_map_key(key)
                        .map_err(StoreError::Invalid)?;
                    entries.push((key.clone(), Self::value_to_param(item)?));
                }
                Param::Map(entries)
            }
        })
    }

    /// Chuẩn bị query+params như `_prepare_falkordb_query`.
    fn prepare(
        query: &str,
        parameters: &BTreeMap<String, Value>,
    ) -> (String, BTreeMap<String, Param>, Result<(), StoreError>) {
        let normalized = normalize_call_importing_subqueries(query);
        let scoped = prepare_project_scope_parameters(parameters);
        let mut params: BTreeMap<String, Param> = BTreeMap::new();
        let mut err = Ok(());
        for (key, value) in &scoped {
            match Self::value_to_param(value) {
                Ok(param) => {
                    params.insert(key.clone(), param);
                }
                Err(e) => err = Err(e),
            }
        }
        match rewrite_datetime_call(&normalized, &scoped, "${param}", "__falkordb_now") {
            Some(rewrite) => {
                params.insert(
                    rewrite.param_name,
                    Param::Str(rewrite.param_value),
                );
                (rewrite.query, params, err)
            }
            None => (normalized, params, err),
        }
    }
}

impl GraphStore for FalkorDbStore {
    fn provider(&self) -> &'static str {
        "falkordb"
    }

    fn execute_query(
        &mut self,
        query: &str,
        parameters: &BTreeMap<String, Value>,
        database: Option<&str>,
    ) -> Result<QueryRecords, StoreError> {
        let graph = self.graph_for(database).to_string();
        let (prepared, params, convert_err) = Self::prepare(query, parameters);
        convert_err?;
        let result = self.client.query(&graph, &prepared, &params, None)?;
        let mut records: Vec<Row> = Vec::with_capacity(result.records.len());
        for row in &result.records {
            let mut map = serde_json::Map::new();
            for (column, value) in result.header.iter().zip(row.iter()) {
                map.insert(column.name.clone(), value.to_json_value());
            }
            records.push(map);
        }
        Ok(records)
    }

    fn ensure_schema(&mut self, database: Option<&str>) -> Result<(), StoreError> {
        if self.schema_ready {
            return Ok(());
        }
        crate::preflight::ensure_schema(self, database)?;
        self.schema_ready = true;
        Ok(())
    }

    fn inspect_indexes(
        &mut self,
        database: Option<&str>,
    ) -> Result<Vec<BTreeMap<String, Value>>, StoreError> {
        // Port của FalkorDBDriver.inspect_indexes: CALL db.indexes() → chuẩn
        // hoá 1 record/property (FalkorDB gom mọi attribute của label vào 1
        // hàng; đó là các range index đơn-thuộc-tính độc lập).
        let records = self.execute_query("CALL db.indexes()", &BTreeMap::new(), database)?;
        let mut normalized = Vec::new();
        for record in &records {
            let label_value = record
                .get("label")
                .or_else(|| record.get("labelsOrTypes"))
                .cloned()
                .unwrap_or(Value::Null);
            let label = match &label_value {
                Value::Array(items) => items
                    .first()
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                Value::String(s) => s.clone(),
                _ => String::new(),
            };
            let properties_value = record
                .get("properties")
                .or_else(|| record.get("property"))
                .cloned()
                .unwrap_or(Value::Array(vec![]));
            let mut properties: Vec<String> = match &properties_value {
                Value::String(s) => vec![s.clone()],
                Value::Array(items) => items
                    .iter()
                    .filter_map(|item| item.as_str().map(str::to_string))
                    .collect(),
                _ => vec![],
            };
            let type_map = record.get("types").cloned().unwrap_or(Value::Null);
            if properties.is_empty()
                && let Value::Object(map) = &type_map
            {
                properties = map.keys().cloned().collect();
            }
            let common_type = record
                .get("index_type")
                .or_else(|| record.get("type"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_lowercase();
            let entity_type = record
                .get("entity_type")
                .or_else(|| record.get("entitytype"))
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .unwrap_or("node")
                .to_lowercase();
            let status = record
                .get("status")
                .or_else(|| record.get("state"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_uppercase();

            for prop in &properties {
                let mut index_types = if common_type.is_empty() {
                    vec![]
                } else {
                    vec![common_type.clone()]
                };
                if index_types.is_empty()
                    && let Value::Object(map) = &type_map
                    && let Some(values) = map.get(prop)
                {
                    let mut collected: Vec<String> = match values {
                        Value::Array(items) => items
                            .iter()
                            .filter_map(|v| v.as_str().map(str::to_string))
                            .collect(),
                        Value::String(s) => vec![s.clone()],
                        _ => vec![],
                    };
                    collected.sort();
                    collected.dedup();
                    index_types = collected
                        .iter()
                        .map(|s| s.to_lowercase())
                        .filter(|s| !s.is_empty())
                        .collect();
                }
                if index_types.is_empty() {
                    index_types.push(String::new());
                }
                for index_type in index_types {
                    let index_type = if index_type == "btree" {
                        "range".to_string()
                    } else {
                        index_type
                    };
                    let mut row = BTreeMap::new();
                    row.insert("label".to_string(), Value::String(label.clone()));
                    row.insert(
                        "properties".to_string(),
                        Value::Array(vec![Value::String(prop.clone())]),
                    );
                    row.insert("index_type".to_string(), Value::String(index_type));
                    row.insert("entity_type".to_string(), Value::String(entity_type.clone()));
                    row.insert("status".to_string(), Value::String(status.clone()));
                    normalized.push(row);
                }
            }
        }
        Ok(normalized)
    }

    fn create_indexes(
        &mut self,
        indexes: &[IndexSpec],
        database: Option<&str>,
    ) -> Result<(), StoreError> {
        // Port của create_indexes: range → create_node_range_index,
        // fulltext → create_node_fulltext_index; swallow "already indexed".
        for index in indexes {
            let label = cortex_graph_core::schema_manifest::validate_cypher_identifier(
                &index.label,
                "label",
            )
            .map_err(StoreError::Invalid)?;
            let prop = cortex_graph_core::schema_manifest::validate_cypher_identifier(
                &index.property,
                "property",
            )
            .map_err(StoreError::Invalid)?;
            let keyword = if index.index_type == "fulltext" {
                "FULLTEXT "
            } else {
                ""
            };
            let query =
                format!("CREATE {keyword}INDEX FOR (e:{label}) ON (e.{prop})");
            let result: Result<QueryRecords, StoreError> =
                self.execute_query(&query, &BTreeMap::new(), database);
            if let Err(StoreError::Falkor(ClientError::Server(message))) = &result
                && message.to_lowercase().contains("already indexed")
            {
                continue;
            }
            result?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn value_to_param_map_roundtrip_shape() {
        let mut row = serde_json::Map::new();
        row.insert("id".to_string(), Value::String("a".into()));
        row.insert("n".to_string(), Value::Number(1.into()));
        let param = FalkorDbStore::value_to_param(&Value::Object(row)).unwrap();
        match param {
            Param::Map(entries) => {
                assert_eq!(entries[0].0, "id");
                assert!(matches!(entries[0].1, Param::Str(_)));
                assert!(matches!(entries[1].1, Param::Int(1)));
            }
            other => panic!("expected map, got {other:?}"),
        }
    }

    #[test]
    fn map_key_backtick_rejected() {
        let mut row = serde_json::Map::new();
        row.insert("a`b".to_string(), Value::Null);
        assert!(FalkorDbStore::value_to_param(&Value::Object(row)).is_err());
    }

    #[test]
    fn prepare_rewrites_datetime_and_scope() {
        let mut params = BTreeMap::new();
        params.insert("project_id".to_string(), Value::String("Bank".into()));
        let (query, out, _) = FalkorDbStore::prepare(
            "MERGE (n:N {id: 'x'}) SET n.updated_at = datetime()",
            &params,
        );
        assert!(query.contains("$__falkordb_now"));
        assert!(out.contains_key("project_id_normalized"));
        assert!(matches!(
            out.get("__falkordb_now"),
            Some(Param::Str(_))
        ));
    }
}

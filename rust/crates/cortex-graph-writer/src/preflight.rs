//! Port của `tools/graph/schema/preflight.py` — automatic, fail-closed graph
//! schema preflight: inspect → create missing required → poll đến ONLINE.

use std::collections::BTreeMap;
use std::time::{Duration, Instant};

use serde_json::Value;

use cortex_graph_core::schema_manifest::{code_graph_schema, GraphSchemaManifest};

use crate::store::{GraphStore, IndexSpec, StoreError};

const READY_STATES: [&str; 3] = ["ONLINE", "OPERATIONAL", "READY"];
const FAILED_STATES: [&str; 3] = ["FAILED", "FAILURE", "ERROR"];

/// `SchemaEnsureResult`.
#[derive(Debug, Clone)]
pub struct SchemaEnsureResult {
    pub manifest: String,
    pub fingerprint: String,
    pub database: Option<String>,
    pub required_count: usize,
    pub verified_count: usize,
    pub elapsed_seconds: f64,
}

fn index_record(record: &BTreeMap<String, Value>) -> (String, Vec<String>, String, String, String) {
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
        .unwrap_or(Value::Null);
    let properties: Vec<String> = match &properties_value {
        Value::String(s) => vec![s.clone()],
        Value::Array(items) => items
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect(),
        _ => vec![],
    };
    let index_type_raw = record
        .get("index_type")
        .or_else(|| record.get("type"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_lowercase();
    let index_type = if index_type_raw == "btree" {
        "range".to_string()
    } else {
        index_type_raw
    };
    let entity_type = record
        .get("entity_type")
        .or_else(|| record.get("entityType"))
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
    (label, properties, index_type, entity_type, status)
}

type IndexKey = (String, Vec<String>, String, String);

fn available_status(records: &[BTreeMap<String, Value>]) -> BTreeMap<IndexKey, String> {
    let mut available = BTreeMap::new();
    for record in records {
        let (label, properties, index_type, entity_type, status) = index_record(record);
        available.insert((label, properties, index_type, entity_type), status);
    }
    available
}

/// `ensure_schema` — create + verify required indexes trước mutation đầu tiên.
/// (Không có asyncio: poll đồng bộ với deadline.)
pub fn ensure_schema(
    store: &mut dyn GraphStore,
    database: Option<&str>,
) -> Result<SchemaEnsureResult, StoreError> {
    ensure_schema_with(store, &code_graph_schema(), database, Duration::from_secs(60))
}

/// Bản manifest-tường-minh của `ensure_schema` (unit-testable).
pub fn ensure_schema_with(
    store: &mut dyn GraphStore,
    manifest: &GraphSchemaManifest,
    database: Option<&str>,
    timeout: Duration,
) -> Result<SchemaEnsureResult, StoreError> {
    let started = Instant::now();
    let deadline = started + timeout;

    if store.provider() == "falkordb" {
        let unsupported: Vec<&cortex_graph_core::schema_manifest::SchemaIndex> = manifest
            .indexes
            .iter()
            .filter(|index| index.properties.len() != 1)
            .collect();
        if !unsupported.is_empty() {
            let detail = unsupported
                .iter()
                .map(|index| format!("{}({})", index.label, index.properties.join(",")))
                .collect::<Vec<_>>()
                .join(", ");
            return Err(StoreError::Schema(format!(
                "FalkorDB schema manifests require single-property indexes: {detail}"
            )));
        }
    }

    let initial_records = store.inspect_indexes(database)?;
    let available = available_status(&initial_records);

    // Required indexes đã FAILED từ đầu → fail ngay (không create lại).
    let failed_initial: Vec<String> = manifest
        .indexes
        .iter()
        .filter(|index| {
            index.required
                && available
                    .get(&(
                        index.label.clone(),
                        index.properties.clone(),
                        index.index_type.clone(),
                        index.entity_type.clone(),
                    ))
                    .map(|status| FAILED_STATES.contains(&status.as_str()))
                    .unwrap_or(false)
        })
        .map(|index| {
            format!(
                "{}({})/{}",
                index.label,
                index.properties.join(","),
                index.index_type
            )
        })
        .collect();
    if !failed_initial.is_empty() {
        return Err(StoreError::Schema(format!(
            "schema {}@{} contains failed required indexes: {}",
            manifest.name,
            manifest.fingerprint(),
            failed_initial.join(", ")
        )));
    }

    let key = |index: &cortex_graph_core::schema_manifest::SchemaIndex| -> IndexKey {
        (
            index.label.clone(),
            index.properties.clone(),
            index.index_type.clone(),
            index.entity_type.clone(),
        )
    };

    let missing_required: Vec<&cortex_graph_core::schema_manifest::SchemaIndex> = manifest
        .indexes
        .iter()
        .filter(|index| index.required && !available.contains_key(&key(index)))
        .collect();
    let missing_optional: Vec<&cortex_graph_core::schema_manifest::SchemaIndex> = manifest
        .indexes
        .iter()
        .filter(|index| !index.required && !available.contains_key(&key(index)))
        .collect();

    for index in &missing_required {
        if Instant::now() >= deadline {
            return Err(StoreError::Schema(format!(
                "schema {}@{} creation exceeded {:.1}s deadline before {}",
                manifest.name,
                manifest.fingerprint(),
                timeout.as_secs_f64(),
                index.label
            )));
        }
        store.create_indexes(
            &[IndexSpec {
                label: index.label.clone(),
                property: index.properties[0].clone(),
                index_type: index.index_type.clone(),
            }],
            database,
        )?;
    }

    for index in &missing_optional {
        if Instant::now() >= deadline {
            break;
        }
        let _ = store.create_indexes(
            &[IndexSpec {
                label: index.label.clone(),
                property: index.properties[0].clone(),
                index_type: index.index_type.clone(),
            }],
            database,
        );
    }

    // Poll đến khi mọi required index ONLINE.
    let mut inspected = initial_records;
    if !missing_required.is_empty() {
        inspected = store.inspect_indexes(database)?;
    }
    loop {
        let available = available_status(&inspected);
        let mut pending: Vec<String> = Vec::new();
        let mut failed: Vec<String> = Vec::new();
        for index in manifest.indexes.iter().filter(|index| index.required) {
            let status = available
                .get(&key(index))
                .map(String::as_str)
                .unwrap_or("");
            if FAILED_STATES.contains(&status) {
                failed.push(format!(
                    "{}({})/{}={status}",
                    index.label,
                    index.properties.join(","),
                    index.index_type
                ));
            } else if !READY_STATES.contains(&status) {
                pending.push(format!(
                    "{}({})",
                    index.label,
                    index.properties.join(",")
                ));
            }
        }
        if !failed.is_empty() {
            return Err(StoreError::Schema(format!(
                "schema {}@{} contains failed required indexes: {}",
                manifest.name,
                manifest.fingerprint(),
                failed.join(", ")
            )));
        }
        if pending.is_empty() {
            break;
        }
        if Instant::now() >= deadline {
            return Err(StoreError::Schema(format!(
                "schema {}@{} not operational before {:.1}s deadline: {}",
                manifest.name,
                manifest.fingerprint(),
                timeout.as_secs_f64(),
                pending.join(", ")
            )));
        }
        std::thread::sleep(Duration::from_millis(100));
        inspected = store.inspect_indexes(database)?;
    }

    let required_count = manifest.indexes.iter().filter(|index| index.required).count();
    Ok(SchemaEnsureResult {
        manifest: manifest.name.clone(),
        fingerprint: manifest.fingerprint(),
        database: database.map(str::to_string),
        required_count,
        verified_count: required_count,
        elapsed_seconds: started.elapsed().as_secs_f64(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn index_record_normalizes_labels_and_types() {
        let mut record = BTreeMap::new();
        record.insert("label".to_string(), Value::String("Function".into()));
        record.insert(
            "properties".to_string(),
            Value::Array(vec![Value::String("id".into())]),
        );
        record.insert("index_type".to_string(), Value::String("btree".into()));
        record.insert("status".to_string(), Value::String("online".into()));
        let (label, properties, index_type, entity_type, status) = index_record(&record);
        assert_eq!(label, "Function");
        assert_eq!(properties, vec!["id".to_string()]);
        assert_eq!(index_type, "range");
        assert_eq!(entity_type, "node");
        assert_eq!(status, "ONLINE");
    }

}

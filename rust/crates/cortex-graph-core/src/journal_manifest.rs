//! Manifest staging — Phase 02 của plans/260913-2130-rust-full-migration.
//!
//! Port của `sqlite_store.py::_manifest_candidates` + `_stage_manifests_locked`
//! (+ `_typed_identity`): suy ra canonical identities payload-free từ artifact
//! rows theo `reconciliation` mode, staging vào `node_manifest`/`edge_manifest`/
//! `edge_endpoint`, cập nhật counters `producer_completion`.

use std::collections::BTreeMap;

use rusqlite::Connection;
use serde_json::{json, Value};

use crate::identity::{canonical_json, sha256_hex};
use crate::models::{
    BatchSpec, JournalError, ManifestDisposition, ProducerStatus, RunRecord, TerminalErrorCode,
};

pub(crate) const PRODUCERS_COMPLETE_ID: &str = "__journal_all_producers_complete__";

/// Thứ tự counters khớp Python `counts` dict (dùng positional UPDATE).
pub(crate) const COUNTER_KEYS: [&str; 10] = [
    "node_emitted",
    "node_unique",
    "node_duplicate",
    "node_conflict",
    "node_rejected",
    "edge_emitted",
    "edge_unique",
    "edge_duplicate",
    "edge_conflict",
    "edge_rejected",
];

fn sqlite_err(error: rusqlite::Error) -> JournalError {
    JournalError::new(
        TerminalErrorCode::InvalidContract,
        format!("journal transaction failed: {error}"),
    )
}

/// `_typed_identity`: scalar JSON → (type, canonical json). Non-scalar → lỗi.
pub fn typed_identity(value: &Value) -> Result<(String, String), String> {
    match value {
        Value::Null | Value::Object(_) | Value::Array(_) => Err(
            "graph identity must be a non-null JSON scalar".to_string(),
        ),
        Value::Bool(b) => Ok(("boolean".to_string(), b.to_string())),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(("integer".to_string(), i.to_string()))
            } else {
                let f = n.as_f64().unwrap_or_default();
                Ok(("number".to_string(), py_float_repr(f)))
            }
        }
        Value::String(s) => {
            let encoded = canonical_json(&Value::String(s.clone()));
            let json_text = String::from_utf8(encoded)
                .map_err(|e| format!("canonical json: {e}"))?;
            Ok(("string".to_string(), json_text))
        }
    }
}

/// json.dumps(float) — Python in "1.5", "1.0", "inf" bị chặn ở canonical_json.
fn py_float_repr(f: f64) -> String {
    if f == f.trunc() && f.abs() < 1e16 {
        format!("{f:.1}")
    } else {
        format!("{f}")
    }
}

/// `str(x)` của Python cho giá trị JSON (chỉ dùng cho text field coerce).
fn py_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Null => "None".to_string(),
        Value::Number(n) => n.to_string(),
        other => other.to_string(),
    }
}

/// `operation.get(key) or fallback` — rỗng/falsy rơi xuống fallback như Python.
fn op_str(operation: &BTreeMap<String, Value>, key: &str, fallback: &str) -> String {
    match operation.get(key) {
        Some(Value::String(s)) if !s.is_empty() => s.clone(),
        Some(Value::Number(n)) => py_str(&Value::Number(n.clone())),
        _ => fallback.to_string(),
    }
}

fn row_str<'a>(row: &'a Value, key: &str) -> Option<&'a Value> {
    row.get(key)
}

/// Ứng viên manifest: mô tả identity canonical của 1 row (node hoặc edge).
#[derive(Debug, Clone)]
pub struct ManifestCandidate {
    pub kind: &'static str,
    pub manifest_id: String,
    pub producer_id: String,
    pub row_ordinal: i64,
    pub payload_digest: String,
    pub disposition: String,
    pub endpoints: Vec<(String, String, String, String, String)>,
    pub scope: Option<String>,
    pub node_label: String,
    pub identity_property: String,
    pub relationship_type: String,
    pub identity_type: String,
    pub identity_json: String,
}

impl ManifestCandidate {
    fn base(
        row: &Value,
        ordinal: usize,
        kind: &'static str,
        producer_id: &str,
        job_id: &str,
    ) -> Self {
        // Digest payload không chứa `_contract_*` (provenance nội bộ).
        let digest_row: Value = match row {
            Value::Object(map) => Value::Object(
                map.iter()
                    .filter(|(k, _)| !k.starts_with("_contract_"))
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect(),
            ),
            other => other.clone(),
        };
        let payload_digest = sha256_hex(&canonical_json(&digest_row));
        let manifest_id = sha256_hex(&canonical_json(&json!({
            "job_id": job_id,
            "kind": kind,
            "row": ordinal as i64,
        })));
        Self {
            kind,
            manifest_id,
            producer_id: producer_id.to_string(),
            row_ordinal: ordinal as i64,
            payload_digest,
            disposition: ManifestDisposition::StagedUnique.value().to_string(),
            endpoints: Vec::new(),
            scope: None,
            node_label: String::new(),
            identity_property: String::new(),
            relationship_type: String::new(),
            identity_type: String::new(),
            identity_json: String::new(),
        }
    }
}

fn reject_candidate(candidate: &mut ManifestCandidate, scope: Option<String>, rel_fallback: &str) {
    candidate.scope = scope;
    if candidate.kind == "edge" {
        candidate.relationship_type = rel_fallback.to_string();
    }
    candidate.identity_type = "invalid".to_string();
    candidate.identity_json = "null".to_string();
    candidate.disposition = ManifestDisposition::Rejected.value().to_string();
}

/// `_manifest_candidates`: (producer_id, candidates). Trả `(None, _)` cho
/// operation rỗng — caller low-level không có row-level conservation.
pub fn manifest_candidates(
    run: &RunRecord,
    spec: &BatchSpec,
    rows: &[Value],
    job_id: &str,
) -> (Option<String>, Vec<ManifestCandidate>) {
    let operation = &spec.operation;
    if operation.is_empty() {
        return (None, Vec::new());
    }
    let producer_id = op_str(operation, "producer_id", &op_str(operation, "label", &spec.operation_key));
    let reconciliation = op_str(operation, "reconciliation", "unsupported");
    let scope_default = run.metadata.project_id.clone();
    let mut candidates = Vec::new();

    for (ordinal, raw) in rows.iter().enumerate() {
        let empty = Value::Object(Default::default());
        let row = if raw.is_object() { raw } else { &empty };
        let scope = py_str(row_str(row, "project_id_normalized").or_else(|| row_str(row, "project_id")).unwrap_or(&Value::String(scope_default.clone())));

        let mutation_kind = op_str(operation, "mutation_kind", "merge");
        if reconciliation == "node_identity" && mutation_kind == "merge" {
            let mut candidate = ManifestCandidate::base(raw, ordinal, "node", &producer_id, job_id);
            candidate.scope = Some(scope.clone());
            candidate.node_label = op_str(operation, "node_label", "");
            candidate.identity_property = op_str(operation, "identity_property", "");
            let identity_result = (|| -> Result<(), String> {
                let row_property = op_str(operation, "row_identity_property", "id");
                let value = row_str(row, &row_property)
                    .ok_or_else(|| "missing row identity".to_string())?;
                let (identity_type, identity_json) = typed_identity(value)?;
                if candidate.node_label.is_empty() || candidate.identity_property.is_empty() {
                    return Err("node identity descriptor is incomplete".to_string());
                }
                candidate.identity_type = identity_type;
                candidate.identity_json = identity_json;
                // Type external: file_path là provenance, không thuộc payload
                // identity → declared duplicate qua digest semantic row.
                if candidate.node_label == "Type"
                    && py_str(row_str(row, "kind").unwrap_or(&Value::Null)).to_lowercase()
                        == *"external"
                {
                    let mut semantic_row = match row {
                        Value::Object(map) => map.clone(),
                        _ => Default::default(),
                    };
                    semantic_row.remove("file_path");
                    candidate.payload_digest =
                        sha256_hex(&canonical_json(&Value::Object(semantic_row)));
                }
                Ok(())
            })();
            if identity_result.is_err() {
                // scope đã set trước khi thử identity (khớp Python: update()
                // scope/node_label/identity_property chạy trước try) — chỉ
                // đánh dấu invalid + REJECTED.
                candidate.identity_type = "invalid".to_string();
                candidate.identity_json = "null".to_string();
                candidate.disposition = ManifestDisposition::Rejected.value().to_string();
            }
            candidates.push(candidate);
            continue;
        }
        if matches!(reconciliation.as_str(), "file_cleanup" | "orphan_unknown_cleanup") {
            continue;
        }

        let mut candidate = ManifestCandidate::base(raw, ordinal, "edge", &producer_id, job_id);
        let edge_result = (|| -> Result<(), String> {
            let (source_label, source_property, source_value): (String, String, &Value);
            let (target_label, target_property, target_value): (String, String, &Value);
            let relationship_type: String;
            let edge_property: String;
            let edge_value: Option<&Value>;
            match reconciliation.as_str() {
                "typed_relationship" | "evidence_edge" => {
                    source_label = py_str(row_str(row, "source_label").ok_or("source_label")?);
                    target_label = py_str(row_str(row, "target_label").ok_or("target_label")?);
                    source_property = op_str_map(row, "source_property", "id");
                    target_property = op_str_map(row, "target_property", "id");
                    source_value = row_str(row, "source_id").ok_or("source_id")?;
                    target_value = row_str(row, "target_id").ok_or("target_id")?;
                    relationship_type = py_str(row_str(row, "rel_type").ok_or("rel_type")?);
                    edge_property = op_str_map(row, "edge_property", "");
                    if edge_property.is_empty() {
                        edge_value = None;
                    } else {
                        let value = row_str(row, "edge_id").ok_or("edge_id")?;
                        if value.is_null() || value == &Value::String(String::new()) {
                            return Err("keyed edge is missing its identity".to_string());
                        }
                        edge_value = Some(value);
                    }
                }
                "repository_file" => {
                    source_label = "Repository".to_string();
                    source_property = "name".to_string();
                    source_value = row_str(row, "repo").ok_or("repo")?;
                    target_label = "File".to_string();
                    target_property = "id".to_string();
                    target_value = row_str(row, "id").ok_or("id")?;
                    relationship_type = "HAS_FILE".to_string();
                    edge_property = String::new();
                    edge_value = None;
                }
                "call_edge" | "call_site" | "possible_call_site" => {
                    source_label = "Function".to_string();
                    source_property = "id".to_string();
                    source_value = row_str(row, "caller_id").ok_or("caller_id")?;
                    target_label = "Function".to_string();
                    target_property = "id".to_string();
                    target_value = row_str(row, "callee_id").ok_or("callee_id")?;
                    relationship_type = if reconciliation == "possible_call_site" {
                        "POSSIBLE_CALLS".to_string()
                    } else {
                        "CALLS".to_string()
                    };
                    edge_property = if reconciliation != "call_edge" {
                        "site_id".to_string()
                    } else {
                        String::new()
                    };
                    if edge_property.is_empty() {
                        edge_value = None;
                    } else {
                        let value = row_str(row, "site_id").ok_or("site_id")?;
                        if value.is_null() || value == &Value::String(String::new()) {
                            return Err("site edge is missing site_id".to_string());
                        }
                        edge_value = Some(value);
                    }
                }
                _ => return Err("unsupported manifest operation".to_string()),
            }
            if source_label.is_empty() || target_label.is_empty() || relationship_type.is_empty() {
                return Err("edge descriptor is incomplete".to_string());
            }
            let (source_type, source_json) = typed_identity(source_value)?;
            let (target_type, target_json) = typed_identity(target_value)?;
            let mut edge_key = serde_json::Map::new();
            edge_key.insert(
                "source".to_string(),
                json!([source_label, source_property, source_type, source_json]),
            );
            edge_key.insert("relationship".to_string(), Value::String(relationship_type.clone()));
            edge_key.insert(
                "target".to_string(),
                json!([target_label, target_property, target_type, target_json]),
            );
            if !edge_property.is_empty() {
                let (edge_type, edge_json) = typed_identity(edge_value.unwrap())?;
                edge_key.insert(
                    "edge".to_string(),
                    json!([edge_property, edge_type, edge_json]),
                );
            }
            candidate.scope = Some(scope.clone());
            candidate.relationship_type = relationship_type;
            candidate.identity_type = "edge_key".to_string();
            candidate.identity_json = String::from_utf8(canonical_json(&Value::Object(edge_key)))
                .map_err(|e| format!("canonical json: {e}"))?;
            candidate.endpoints = vec![
                (
                    "source".to_string(),
                    source_label,
                    source_property,
                    source_type,
                    source_json,
                ),
                (
                    "target".to_string(),
                    target_label,
                    target_property,
                    target_type,
                    target_json,
                ),
            ];
            Ok(())
        })();
        if let Err(_message) = edge_result {
            let rel_fallback = row_str(row, "rel_type")
                .map(py_str)
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| "invalid".to_string());
            reject_candidate(&mut candidate, Some(scope.clone()), &rel_fallback);
        }
        candidates.push(candidate);
    }
    (Some(producer_id), candidates)
}

/// `row.get(key) or default` cho map value (chuỗi).
fn op_str_map(row: &Value, key: &str, default: &str) -> String {
    match row.get(key) {
        Some(Value::String(s)) if !s.is_empty() => s.clone(),
        Some(Value::Number(n)) => py_str(&Value::Number(n.clone())),
        _ => default.to_string(),
    }
}

/// `_stage_manifests_locked`: staging candidates + counters producer.
/// Trả về map failure (conflict/rejected khác 0) như Python.
pub fn stage_manifests_locked(
    conn: &Connection,
    run_id_value: &str,
    job_id: &str,
    producer_id: &str,
    candidates: &[ManifestCandidate],
    now: &str,
) -> Result<BTreeMap<String, i64>, JournalError> {
    let producer_status: Option<String> = conn
        .query_row(
            "SELECT status FROM producer_completion WHERE run_id = ?1 AND producer_id = ?2",
            rusqlite::params![run_id_value, producer_id],
            |row| row.get(0),
        )
        .ok();
    if let Some(status) = producer_status
        && status != ProducerStatus::Open.value() {
            return Err(JournalError::new(
                TerminalErrorCode::InvalidTransition,
                format!("producer {producer_id} is already complete"),
            ));
        }
    conn.execute(
        "INSERT INTO producer_completion(run_id, producer_id, status, updated_at) \
         VALUES (?1, ?2, ?3, ?4) \
         ON CONFLICT(run_id, producer_id) DO UPDATE SET updated_at = excluded.updated_at",
        rusqlite::params![
            run_id_value,
            producer_id,
            ProducerStatus::Open.value(),
            now
        ],
    )
    .map_err(sqlite_err)?;

    let mut counts: BTreeMap<String, i64> = COUNTER_KEYS
        .iter()
        .map(|k| (k.to_string(), 0))
        .collect();

    for candidate in candidates {
        let key_prefix = candidate.kind;
        *counts
            .get_mut(&format!("{key_prefix}_emitted"))
            .expect("counter key") += 1;
        let mut disposition = candidate.disposition.clone();
        let _shape_column = if candidate.kind == "node" {
            "node_label"
        } else {
            "relationship_type"
        };
        let shape_value = if candidate.kind == "node" {
            &candidate.node_label
        } else {
            &candidate.relationship_type
        };
        if disposition == ManifestDisposition::StagedUnique.value() {
            let existing: Option<String> = if candidate.kind == "node" {
                conn.query_row(
                    "SELECT payload_digest FROM node_manifest \
                     WHERE run_id = ?1 AND scope = ?2 AND node_label = ?3 \
                       AND identity_property = ?4 AND identity_type = ?5 AND identity_json = ?6 \
                       AND disposition IN (?7, ?8) LIMIT 1",
                    rusqlite::params![
                        run_id_value,
                        candidate.scope,
                        shape_value,
                        candidate.identity_property,
                        candidate.identity_type,
                        candidate.identity_json,
                        ManifestDisposition::StagedUnique.value(),
                        ManifestDisposition::DeclaredDuplicate.value(),
                    ],
                    |row| row.get(0),
                )
                .ok()
            } else {
                conn.query_row(
                    "SELECT payload_digest FROM edge_manifest \
                     WHERE run_id = ?1 AND scope = ?2 AND relationship_type = ?3 \
                       AND identity_type = ?4 AND identity_json = ?5 \
                       AND disposition IN (?6, ?7) LIMIT 1",
                    rusqlite::params![
                        run_id_value,
                        candidate.scope,
                        shape_value,
                        candidate.identity_type,
                        candidate.identity_json,
                        ManifestDisposition::StagedUnique.value(),
                        ManifestDisposition::DeclaredDuplicate.value(),
                    ],
                    |row| row.get(0),
                )
                .ok()
            };
            if let Some(existing_digest) = existing {
                disposition = if existing_digest == candidate.payload_digest {
                    ManifestDisposition::DeclaredDuplicate.value().to_string()
                } else {
                    ManifestDisposition::Conflict.value().to_string()
                };
            }
        }
        let suffix = disposition.strip_prefix("staged_").unwrap_or(&disposition);
        let suffix = suffix.strip_prefix("declared_").unwrap_or(suffix);
        *counts
            .get_mut(&format!("{key_prefix}_{suffix}"))
            .expect("counter key") += 1;

        if candidate.kind == "node" {
            conn.execute(
                "INSERT INTO node_manifest(\
                    run_id, manifest_id, job_id, producer_id, row_ordinal, scope, \
                    node_label, identity_property, identity_type, identity_json, \
                    payload_digest, disposition, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
                rusqlite::params![
                    run_id_value,
                    candidate.manifest_id,
                    job_id,
                    producer_id,
                    candidate.row_ordinal,
                    candidate.scope,
                    candidate.node_label,
                    candidate.identity_property,
                    candidate.identity_type,
                    candidate.identity_json,
                    candidate.payload_digest,
                    disposition,
                    now,
                    now,
                ],
            )
            .map_err(sqlite_err)?;
        } else {
            conn.execute(
                "INSERT INTO edge_manifest(\
                    run_id, manifest_id, job_id, producer_id, row_ordinal, scope, \
                    relationship_type, identity_type, identity_json, payload_digest, \
                    disposition, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
                rusqlite::params![
                    run_id_value,
                    candidate.manifest_id,
                    job_id,
                    producer_id,
                    candidate.row_ordinal,
                    candidate.scope,
                    candidate.relationship_type,
                    candidate.identity_type,
                    candidate.identity_json,
                    candidate.payload_digest,
                    disposition,
                    now,
                    now,
                ],
            )
            .map_err(sqlite_err)?;
            for (role, label, prop, identity_type, identity_json) in &candidate.endpoints {
                conn.execute(
                    "INSERT INTO edge_endpoint(\
                        run_id, edge_manifest_id, role, scope, node_label, \
                        identity_property, identity_type, identity_json) \
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                    rusqlite::params![
                        run_id_value,
                        candidate.manifest_id,
                        role,
                        candidate.scope,
                        label,
                        prop,
                        identity_type,
                        identity_json,
                    ],
                )
                .map_err(sqlite_err)?;
            }
        }
    }

    conn.execute(
        "UPDATE producer_completion SET \
            node_emitted = node_emitted + ?1, node_unique = node_unique + ?2, \
            node_duplicate = node_duplicate + ?3, node_conflict = node_conflict + ?4, \
            node_rejected = node_rejected + ?5, edge_emitted = edge_emitted + ?6, \
            edge_unique = edge_unique + ?7, edge_duplicate = edge_duplicate + ?8, \
            edge_conflict = edge_conflict + ?9, edge_rejected = edge_rejected + ?10, \
            updated_at = ?11 \
         WHERE run_id = ?12 AND producer_id = ?13",
        rusqlite::params![
            counts["node_emitted"],
            counts["node_unique"],
            counts["node_duplicate"],
            counts["node_conflict"],
            counts["node_rejected"],
            counts["edge_emitted"],
            counts["edge_unique"],
            counts["edge_duplicate"],
            counts["edge_conflict"],
            counts["edge_rejected"],
            now,
            run_id_value,
            producer_id,
        ],
    )
    .map_err(sqlite_err)?;

    Ok(counts
        .into_iter()
        .filter(|(key, value)| {
            *value != 0 && (key.ends_with("_conflict") || key.ends_with("_rejected"))
        })
        .collect())
}

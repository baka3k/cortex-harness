//! Port của `tools.graph.journal.consumer` — required-lane replay driver
//! (phase-03 sync-plane cutover). Contract freeze theo phase-01 M2 probe:
//! **replay = legacy-drain-only** (Rust children không enqueue batch; mọi
//! journal trên disk là legacy).
//!
//! Thành phần port từ Python:
//! * `GraphWriteOperation` descriptor (`operation.py`) — from_dict + operation_key;
//! * trusted compilers (`executor.py` + `query_contract.py` + `reconcile.py`) —
//!   mutation / readback / endpoint-audit;
//! * receipt transform (`guard.py::_receipt_query`) — GraphWriteReceipt node là
//!   bằng chứng durable cho reconciliation readback;
//! * drain loop (`consumer.py::GraphWriteJournalConsumer.drain`) — barrier
//!   node → endpoint-audit → edge, retry/reconcile classes.
//!
//! Deliberate fix so Python consumer (phase-01.2 probe): store target lấy từ
//! `GraphContext` (phase-02) — ladybug hỗ trợ; consumer.py `_main` không có
//! branch ladybug (neo4j → NEO4J else FALKORDB → sai store).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use cortex_graph_core::identity;
use cortex_graph_core::journal::Journal;
use cortex_graph_core::models::{BatchRecord, JournalError, RunRecord};
use cortex_graph_core::models::{
    BarrierStatus, JournalLimits, OperationPhase, RetryClass, RunMetadata, TerminalErrorCode,
};
use cortex_graph_core::schema_manifest::{code_graph_schema, validate_cypher_identifier};
use cortex_graph_writer::store::GraphStore;
use serde_json::{json, Value};

use crate::graphops::GraphContext;
use crate::journalenv;

pub const NODE_PHASE_BARRIER: &str = "phase:nodes";
pub const ENDPOINT_AUDIT_BARRIER: &str = "audit:endpoints";
const NODE_FIRST_QUERY_SHAPE: &str = "language-writer-node-first-v1";

/// Consumer-side config (`journal_config_from_env` port — phần consumer cần).
pub struct ReplayConfig {
    pub path: PathBuf,
    pub metadata: RunMetadata,
    pub limits: JournalLimits,
    pub lease_seconds: i64,
}

impl ReplayConfig {
    pub fn required(&self) -> bool {
        true
    }
}

/// `journal_config_from_env` — mode ∈ REQUIRED_MODES + PATH + METADATA.
/// Trả `Ok(None)` khi mode off/empty (không drain); Err khi mode enabled
/// nhưng path/metadata thiếu hoặc metadata hỏng (parity consumer `_main`).
pub fn replay_config_from_env() -> Result<Option<ReplayConfig>, JournalError> {
    let lookup = |name: &str| {
        std::env::var(name).ok().filter(|v| !v.trim().is_empty())
    };
    let raw_mode = lookup(journalenv::MODE_ENV).unwrap_or_default();
    let mode = journalenv::normalize_mode(&raw_mode).map_err(|message| {
        JournalError::new(TerminalErrorCode::InvalidContract, message)
    })?;
    if mode == "off" {
        return Ok(None);
    }
    let path_text = lookup(journalenv::PATH_ENV).unwrap_or_default();
    let metadata_text = lookup(journalenv::METADATA_ENV).unwrap_or_default();
    if path_text.is_empty() || metadata_text.is_empty() {
        return Err(JournalError::new(
            TerminalErrorCode::InvalidContract,
            "journal mode requires an absolute path and stable run metadata",
        ));
    }
    let metadata_value: Value = serde_json::from_str(&metadata_text).map_err(|error| {
        JournalError::new(
            TerminalErrorCode::InvalidContract,
            format!("invalid graph journal metadata: {error}"),
        )
    })?;
    let metadata: RunMetadata = serde_json::from_value(metadata_value.clone()).map_err(|error| {
        JournalError::new(
            TerminalErrorCode::InvalidContract,
            format!("invalid graph journal metadata: {error}"),
        )
    })?;
    let path = PathBuf::from(path_text);
    if !path.is_absolute() {
        return Err(JournalError::new(
            TerminalErrorCode::InvalidContract,
            "graph journal path must be absolute",
        ));
    }
    Ok(Some(ReplayConfig {
        path,
        metadata,
        limits: JournalLimits::default(),
        lease_seconds: 300,
    }))
}

/// Run id theo `identity.py::run_id` (sha256 canonical metadata).
fn run_id_of(metadata: &RunMetadata) -> Result<String, JournalError> {
    let value = serde_json::to_value(metadata)
        .map_err(|error| JournalError::new(TerminalErrorCode::InvalidContract, error.to_string()))?;
    Ok(identity::run_id(&value))
}

/// Unix epoch seconds (journalx.rs::now_epoch_s tương đương).
pub fn now_epoch_s() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .unwrap_or_default()
}

fn open_journal(path: &Path) -> Result<Journal, JournalError> {
    let artifact_root = path
        .parent()
        .map(|parent| parent.join("artifacts"))
        .unwrap_or_default();
    Journal::open(
        path,
        &artifact_root,
        JournalLimits::default(),
        Box::new(now_epoch_s),
    )
}

/// Debug helper (tests): computed run_id từ metadata JSON.
pub fn debug_run_id(metadata: &serde_json::Value) -> String {
    identity::run_id(metadata)
}

// ── GraphWriteOperation descriptor (operation.py port) ────────────────────

#[derive(Debug, Clone)]
struct GraphWriteOperation {
    label: String,
    phase: OperationPhase,
    version: i64,
    reconciliation: String,
    node_label: Option<String>,
    identity_property: Option<String>,
    row_identity_property: String,
    row_properties_property: Option<String>,
    mutation_kind: String,
    query_fingerprint: Option<String>,
}

impl GraphWriteOperation {
    fn from_dict(value: &Value) -> Result<Self, JournalError> {
        let object = value.as_object().ok_or_else(|| {
            JournalError::new(
                TerminalErrorCode::InvalidContract,
                "operation descriptor must be an object",
            )
        })?;
        let text = |key: &str| -> Result<String, JournalError> {
            object
                .get(key)
                .and_then(Value::as_str)
                .map(str::to_string)
                .ok_or_else(|| {
                    JournalError::new(
                        TerminalErrorCode::InvalidContract,
                        format!("operation descriptor missing {key}"),
                    )
                })
        };
        let optional_text = |key: &str| {
            object
                .get(key)
                .and_then(Value::as_str)
                .filter(|v| !v.is_empty())
                .map(str::to_string)
        };
        let phase_value = text("phase")?;
        let phase = OperationPhase::from_value(&phase_value).ok_or_else(|| {
            JournalError::new(
                TerminalErrorCode::InvalidContract,
                format!("unknown operation phase {phase_value}"),
            )
        })?;
        let operation = Self {
            label: text("label")?,
            phase,
            version: object.get("version").and_then(Value::as_i64).unwrap_or(1),
            reconciliation: object
                .get("reconciliation")
                .and_then(Value::as_str)
                .unwrap_or("unsupported")
                .to_string(),
            node_label: optional_text("node_label"),
            identity_property: optional_text("identity_property"),
            row_identity_property: object
                .get("row_identity_property")
                .and_then(Value::as_str)
                .unwrap_or("id")
                .to_string(),
            row_properties_property: optional_text("row_properties_property"),
            mutation_kind: object
                .get("mutation_kind")
                .and_then(Value::as_str)
                .unwrap_or("merge")
                .to_string(),
            query_fingerprint: optional_text("query_fingerprint"),
        };
        if let Some(expected) = object.get("operation_key").and_then(Value::as_str)
            && expected != operation.operation_key()
        {
            return Err(JournalError::new(
                TerminalErrorCode::InvalidContract,
                "persisted operation key does not match its descriptor",
            ));
        }
        Ok(operation)
    }

    fn operation_key(&self) -> String {
        match &self.query_fingerprint {
            Some(fingerprint) => format!(
                "graph-write/v{}/{}/{}/{}",
                self.version, self.phase.value(), self.label, fingerprint
            ),
            None => format!("graph-write/v{}/{}/{}", self.version, self.phase.value(), self.label),
        }
    }
}

fn invalid_contract(message: impl Into<String>) -> JournalError {
    JournalError::new(TerminalErrorCode::InvalidContract, message)
}

fn validate_identifier(value: &str, kind: &str) -> Result<(), JournalError> {
    validate_cypher_identifier(value, kind)
        .map(|_: String| ())
        .map_err(invalid_contract)
}

fn project_scope(value: Option<&str>) -> Result<String, JournalError> {
    let normalized = value.unwrap_or_default().trim();
    if normalized.is_empty() {
        Err(invalid_contract("row requires project_id"))
    } else {
        Ok(normalized.to_string())
    }
}

// ── Trusted compilers (executor.py + reconcile.py + query_contract.py) ────

fn materialize_rows(rows: &[Value]) -> Vec<Value> {
    rows.to_vec()
}

/// `compile_persisted_mutation` — allowlisted replay compilers; Cypher lưu
/// trong descriptor không bao giờ được thực thi.
fn compile_persisted_mutation(
    operation: &GraphWriteOperation,
    rows: &[Value],
) -> Result<(String, BTreeMap<String, Value>), JournalError> {
    let materialized = materialize_rows(rows);
    let unsupported = || {
        invalid_contract(format!(
            "operation {} has no trusted replay compiler",
            operation.operation_key()
        ))
    };
    match operation.reconciliation.as_str() {
        "node_identity" => {
            let node_label = operation.node_label.as_deref().ok_or_else(unsupported)?;
            let identity = operation.identity_property.as_deref().ok_or_else(unsupported)?;
            validate_identifier(node_label, "node label")?;
            validate_identifier(identity, "identity property")?;
            validate_identifier(&operation.row_identity_property, "row identity property")?;
            let row_value = match &operation.row_properties_property {
                Some(prop) => {
                    validate_identifier(prop, "row properties property")?;
                    format!("coalesce(row.{prop}, {{}})")
                }
                None => "row".to_string(),
            };
            let verb = if operation.mutation_kind == "merge" { "MERGE" } else { "MATCH" };
            let query = format!(
                "UNWIND $rows AS row {verb} (n:{node_label} {{{identity}: row.{}}}) \
                 SET n += {row_value}, n.updated_at = datetime() \
                 RETURN count(n) AS count",
                operation.row_identity_property
            );
            Ok((query, rows_param(materialized)))
        }
        "typed_relationship" => {
            let groups = group_typed_relations(&materialized)?;
            if groups.len() != 1 {
                return Err(invalid_contract(
                    "persisted relationship batch must contain one endpoint/type triple",
                ));
            }
            let (group, grouped_rows) = groups.into_iter().next().expect("one group");
            let query = compile_relationship_upsert(&group)?;
            Ok((query, rows_param(grouped_rows)))
        }
        "evidence_edge" => {
            let groups = group_evidence_edges(&materialized)?;
            if groups.len() != 1 {
                return Err(invalid_contract(
                    "persisted evidence-edge batch must contain one edge shape",
                ));
            }
            let (group, grouped_rows) = groups.into_iter().next().expect("one group");
            let query = compile_evidence_edge_upsert(&group)?;
            Ok((query, rows_param(grouped_rows)))
        }
        "repository_file" => Ok((
            "UNWIND $rows AS row \
             MATCH (repository:Repository {name: row.repo, \
             project_id_normalized: row.project_id_normalized}) \
             MATCH (file:File {id: row.id, \
             project_id_normalized: row.project_id_normalized}) \
             MERGE (repository)-[edge:HAS_FILE]->(file) \
             RETURN count(edge) AS count"
                .to_string(),
            rows_param(materialized),
        )),
        "call_edge" => Ok((
            "UNWIND $rows AS row \
             MATCH (caller:Function {id: row.caller_id, \
             project_id_normalized: row.project_id_normalized}) \
             MATCH (callee:Function {id: row.callee_id, \
             project_id_normalized: row.project_id_normalized}) \
             MERGE (caller)-[edge:CALLS]->(callee) \
             SET edge.count = row.count, edge.call_type = row.call_type, \
             edge.project_id = row.project_id, \
             edge.project_id_normalized = row.project_id_normalized, \
             edge.updated_at = datetime() \
             RETURN count(edge) AS count"
                .to_string(),
            rows_param(materialized),
        )),
        "call_site" | "possible_call_site" => {
            let rel = if operation.reconciliation == "call_site" { "CALLS" } else { "POSSIBLE_CALLS" };
            Ok((
                format!(
                    "UNWIND $rows AS row \
                     MATCH (caller:Function {{id: row.caller_id, \
                     project_id_normalized: row.project_id_normalized}}) \
                     MATCH (callee:Function {{id: row.callee_id, \
                     project_id_normalized: row.project_id_normalized}}) \
                     MERGE (caller)-[edge:{rel} {{site_id: row.site_id}}]->(callee) \
                     SET edge += coalesce(row.props, {{}}) \
                     RETURN count(edge) AS count"
                ),
                rows_param(materialized),
            ))
        }
        "file_cleanup" => {
            if operation.version != 2 {
                return Err(unsupported());
            }
            let node_label = operation.node_label.as_deref().ok_or_else(unsupported)?;
            validate_identifier(node_label, "node label")?;
            // Ladybug-safe shape (phase-02 dialect): FOREACH-collect-delete
            // không parse được — delete trực tiếp từ MATCH; count(*) đếm số
            // row khớp trước xoá (ngữ nghĩa giữ nguyên).
            Ok((
                format!(
                    "UNWIND $rows AS row \
                     MATCH (n:{node_label}) \
                     WHERE n.project_id = row.project_id \
                     AND (coalesce(n.file_path, '') IN row.paths \
                     OR coalesce(n.path, '') IN row.paths \
                     OR (n:File AND n.id IN row.paths)) \
                     DETACH DELETE n \
                     RETURN count(*) AS count"
                ),
                rows_param(materialized),
            ))
        }
        "orphan_unknown_cleanup" => {
            if operation.version != 2 {
                return Err(unsupported());
            }
            Ok((
                "UNWIND $rows AS row \
                 MATCH (u:UnknownFunction) \
                 WHERE u.project_id = row.project_id \
                 AND NOT ()-[:UNKNOWN_CALL]->(u) \
                 DETACH DELETE u \
                 RETURN count(*) AS count"
                    .to_string(),
                rows_param(materialized),
            ))
        }
        _ => Err(unsupported()),
    }
}

fn rows_param(rows: Vec<Value>) -> BTreeMap<String, Value> {
    let mut params = BTreeMap::new();
    params.insert("rows".to_string(), Value::Array(rows));
    params
}

// ── query_contract.py port: typed relations ───────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct RelationshipGroup {
    source_label: String,
    target_label: String,
    relationship_type: String,
}

fn has_identity_index(label: &str, property: &str) -> bool {
    code_graph_schema()
        .indexes
        .iter()
        .any(|index| index.label == label && index.properties.iter().any(|p| p == property))
}

fn project_id_lookup_key(value: &str) -> Option<String> {
    let normalized = value.trim();
    if normalized.is_empty() {
        None
    } else {
        Some(normalized.to_lowercase())
    }
}

type TypedGroups = BTreeMap<RelationshipGroup, Vec<Value>>;

fn group_typed_relations(relations: &[Value]) -> Result<TypedGroups, JournalError> {
    let mut groups: TypedGroups = BTreeMap::new();
    for (position, relation) in relations.iter().enumerate() {
        let as_object = relation.as_object().ok_or_else(|| {
            invalid_contract(format!("typed relationship row {position} must be an object"))
        })?;
        let text = |key: &str| -> Result<String, JournalError> {
            as_object.get(key).and_then(Value::as_str).map(str::to_string).ok_or_else(|| {
                invalid_contract(format!(
                    "typed relationship row requires {key} (row {position})"
                ))
            })
        };
        let source_label = text("source_label")?;
        let target_label = text("target_label")?;
        let relationship_type = text("rel_type")?;
        if as_object.get("source_id").and_then(Value::as_str).unwrap_or("").is_empty()
            || as_object.get("target_id").and_then(Value::as_str).unwrap_or("").is_empty()
        {
            return Err(invalid_contract(format!(
                "typed relationship row requires source_id and target_id (row {position})"
            )));
        }
        let mut row = relation.clone();
        let row_object = row.as_object_mut().expect("object");
        let project_id = as_object
            .get("project_id")
            .and_then(Value::as_str)
            .map(str::to_string)
            .or_else(|| {
                as_object
                    .get("properties")
                    .and_then(Value::as_object)
                    .and_then(|properties| properties.get("project_id"))
                    .and_then(Value::as_str)
                    .map(str::to_string)
            });
        let scope = project_scope(project_id.as_deref())?;
        let normalized_scope = project_id_lookup_key(&scope).ok_or_else(|| {
            invalid_contract(format!(
                "typed relationship row requires project_id (row {position})"
            ))
        })?;
        row_object.insert("project_id".into(), json!(scope));
        row_object.insert("project_id_normalized".into(), json!(normalized_scope));
        row_object.insert("_contract_row_position".into(), json!(position));
        for label in [&source_label, &target_label] {
            if !has_identity_index(label, "id") {
                return Err(invalid_contract(format!(
                    "label {label:?} has no required id index in the schema manifest"
                )));
            }
        }
        validate_identifier(&source_label, "source label")?;
        validate_identifier(&target_label, "target label")?;
        validate_identifier(&relationship_type, "relationship type")?;
        groups.entry(RelationshipGroup { source_label, target_label, relationship_type }).or_default().push(row);
    }
    Ok(groups)
}

fn compile_relationship_upsert(group: &RelationshipGroup) -> Result<String, JournalError> {
    validate_identifier(&group.source_label, "source label")?;
    validate_identifier(&group.target_label, "target label")?;
    validate_identifier(&group.relationship_type, "relationship type")?;
    Ok(format!(
        "UNWIND $rows AS row \
         MATCH (a:{0} {{id: row.source_id, project_id_normalized: row.project_id_normalized}}) \
         WITH row, a \
         MATCH (b:{1} {{id: row.target_id, project_id_normalized: row.project_id_normalized}}) \
         MERGE (a)-[r:{2}]->(b) \
         SET r += coalesce(row.properties, {{}}), \
         r.project_id = row.project_id, \
         r.project_id_normalized = row.project_id_normalized \
         RETURN count(r) AS count",
        group.source_label, group.target_label, group.relationship_type
    ))
}

fn compile_relationship_endpoint_audit(group: &RelationshipGroup) -> Result<String, JournalError> {
    Ok(format!(
        "UNWIND $rows AS row \
         OPTIONAL MATCH (a:{0} {{id: row.source_id, project_id_normalized: row.project_id_normalized}}) \
         WITH row, count(a) AS source_matches \
         OPTIONAL MATCH (b:{1} {{id: row.target_id, project_id_normalized: row.project_id_normalized}}) \
         WITH row, source_matches, count(b) AS target_matches \
         WHERE source_matches <> 1 OR target_matches <> 1 \
         RETURN row.source_id AS source_id, row.target_id AS target_id, \
         row.project_id_normalized AS project_id_normalized, \
         source_matches, target_matches LIMIT 20",
        group.source_label, group.target_label
    ))
}

// ── query_contract.py port: evidence edges ────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct EvidenceEdgeGroup {
    source_label: String,
    source_property: String,
    target_label: String,
    target_property: String,
    relationship_type: String,
    edge_property: String,
}

type EvidenceGroups = BTreeMap<EvidenceEdgeGroup, Vec<Value>>;

const EVIDENCE_ROW_FIELDS: [&str; 5] = [
    "source_label",
    "source_property",
    "target_label",
    "target_property",
    "rel_type",
];

fn group_evidence_edges(edges: &[Value]) -> Result<EvidenceGroups, JournalError> {
    let mut groups: EvidenceGroups = BTreeMap::new();
    for (position, edge) in edges.iter().enumerate() {
        let as_object = edge.as_object().ok_or_else(|| {
            invalid_contract(format!("evidence edge row {position} must be an object"))
        })?;
        let text = |key: &str| -> String {
            as_object.get(key).and_then(Value::as_str).unwrap_or_default().to_string()
        };
        let missing: Vec<&str> = EVIDENCE_ROW_FIELDS
            .iter()
            .filter(|field| text(field).trim().is_empty())
            .copied()
            .collect();
        if !missing.is_empty() || text("source_id").trim().is_empty() || text("target_id").is_empty() {
            return Err(invalid_contract(format!(
                "evidence edge row requires source/target label, property, id, and rel_type (row {position})"
            )));
        }
        let mut row = edge.clone();
        let row_object = row.as_object_mut().expect("object");
        let project_id = as_object
            .get("project_id")
            .and_then(Value::as_str)
            .map(str::to_string)
            .or_else(|| {
                as_object
                    .get("props")
                    .and_then(Value::as_object)
                    .and_then(|props| props.get("project_id"))
                    .and_then(Value::as_str)
                    .map(str::to_string)
            });
        let scope = project_scope(project_id.as_deref())?;
        let normalized_scope = project_id_lookup_key(&scope).ok_or_else(|| {
            invalid_contract(format!("evidence edge row requires project_id (row {position})"))
        })?;
        row_object.insert("project_id".into(), json!(scope));
        row_object.insert("project_id_normalized".into(), json!(normalized_scope));
        let edge_property = text("edge_property");
        let edge_id = text("edge_id");
        if !edge_property.is_empty() && edge_id.is_empty() {
            return Err(invalid_contract(format!(
                "evidence edge row with an edge_property requires a non-empty edge_id (row {position})"
            )));
        }
        let edge_id = if edge_id.is_empty() { text("source_id") } else { edge_id };
        row_object.insert("edge_id".into(), json!(edge_id));
        for (kind, identifier) in [
            ("source label", text("source_label")),
            ("target label", text("target_label")),
            ("relationship type", text("rel_type")),
            ("source property", text("source_property")),
            ("target property", text("target_property")),
        ] {
            validate_identifier(&identifier, kind)?;
        }
        if !edge_property.is_empty() {
            validate_identifier(&edge_property, "edge property")?;
        }
        groups
            .entry(EvidenceEdgeGroup {
                source_label: text("source_label"),
                source_property: text("source_property"),
                target_label: text("target_label"),
                target_property: text("target_property"),
                relationship_type: text("rel_type"),
                edge_property,
            })
            .or_default()
            .push(row);
    }
    Ok(groups)
}

fn compile_evidence_edge_upsert(group: &EvidenceEdgeGroup) -> Result<String, JournalError> {
    let edge_pattern = if group.edge_property.is_empty() {
        format!("MERGE (a)-[r:{}]->(b) ", group.relationship_type)
    } else {
        format!(
            "MERGE (a)-[r:{} {{{}: row.edge_id}}]->(b) ",
            group.relationship_type, group.edge_property
        )
    };
    Ok(format!(
        "UNWIND $rows AS row \
         MATCH (a:{0} {{{1}: row.source_id, project_id_normalized: row.project_id_normalized}}) \
         WITH row, a \
         MATCH (b:{2} {{{3}: row.target_id, project_id_normalized: row.project_id_normalized}}) \
         {edge_pattern}\
         SET r += coalesce(row.props, {{}}), \
         r.project_id = row.project_id, \
         r.updated_at = datetime() \
         RETURN count(r) AS count",
        group.source_label,
        group.source_property,
        group.target_label,
        group.target_property
    ))
}

fn compile_evidence_edge_readback(group: &EvidenceEdgeGroup) -> String {
    let edge_match = if group.edge_property.is_empty() {
        format!("-[r:{}]->", group.relationship_type)
    } else {
        format!(
            "-[r:{} {{{}: row.edge_id}}]->",
            group.relationship_type, group.edge_property
        )
    };
    format!(
        "UNWIND $rows AS row \
         OPTIONAL MATCH (a:{0} {{{1}: row.source_id, project_id_normalized: row.project_id_normalized}}) \
         {edge_match}\
         (b:{2} {{{3}: row.target_id, project_id_normalized: row.project_id_normalized}}) \
         RETURN count(r) AS count",
        group.source_label,
        group.source_property,
        group.target_label,
        group.target_property
    )
}

fn compile_evidence_endpoint_audit(group: &EvidenceEdgeGroup) -> String {
    format!(
        "UNWIND $rows AS row \
         OPTIONAL MATCH (a:{0} {{{1}: row.source_id, project_id_normalized: row.project_id_normalized}}) \
         WITH row, count(a) AS source_matches \
         OPTIONAL MATCH (b:{2} {{{3}: row.target_id, project_id_normalized: row.project_id_normalized}}) \
         WITH row, source_matches, count(b) AS target_matches \
         WHERE source_matches <> 1 OR target_matches <> 1 \
         RETURN row.source_id AS source_id, row.target_id AS target_id, \
         row.project_id_normalized AS project_id_normalized, \
         source_matches, target_matches LIMIT 20",
        group.source_label,
        group.source_property,
        group.target_label,
        group.target_property
    )
}

// ── reconcile.py port: readbacks + endpoint audit ─────────────────────────

type CompiledQuery = (String, BTreeMap<String, Value>);

/// `compile_reconciliation_readback`.
fn compile_reconciliation_readback(
    operation: &GraphWriteOperation,
    rows: &[Value],
    job_id: Option<&str>,
    artifact_sha256: &str,
    run_id_value: &str,
    generation: &str,
) -> Result<Option<CompiledQuery>, JournalError> {
    if let Some(job_id) = job_id {
        let mut params = BTreeMap::new();
        params.insert("job_id".to_string(), json!(job_id));
        params.insert("operation_key".to_string(), json!(operation.operation_key()));
        params.insert("expected_count".to_string(), json!(rows.len() as i64));
        params.insert("artifact_sha256".to_string(), json!(artifact_sha256));
        params.insert("run_id".to_string(), json!(run_id_value));
        params.insert("generation".to_string(), json!(generation));
        return Ok(Some((
            "MATCH (receipt:GraphWriteReceipt {id: $job_id}) \
             WHERE receipt.operation_key = $operation_key \
             AND receipt.row_count = $expected_count \
             AND receipt.artifact_sha256 = $artifact_sha256 \
             AND receipt.run_id = $run_id \
             AND receipt.generation = $generation \
             RETURN count(receipt) AS count"
                .to_string(),
            params,
        )));
    }
    match operation.reconciliation.as_str() {
        "node_identity" => {
            let node_label = operation.node_label.clone().unwrap_or_default();
            let identity = operation.identity_property.clone().unwrap_or_default();
            Ok(Some((
                format!(
                    "UNWIND $rows AS row \
                     OPTIONAL MATCH (n:{node_label} {{{identity}: row.{}}}) \
                     RETURN count(n) AS count",
                    operation.row_identity_property
                ),
                rows_param(rows.to_vec()),
            )))
        }
        "typed_relationship" => {
            let groups = group_typed_relations(rows)?;
            if groups.len() != 1 {
                return Err(invalid_contract(
                    "one journal relationship batch must contain one label triple",
                ));
            }
            let (group, _) = groups.into_iter().next().expect("one group");
            Ok(Some((
                format!(
                    "UNWIND $rows AS row \
                     OPTIONAL MATCH (a:{0} {{id: row.source_id}})\
                     -[r:{2}]->\
                     (b:{1} {{id: row.target_id}}) \
                     RETURN count(r) AS count",
                    group.source_label, group.target_label, group.relationship_type
                ),
                rows_param(rows.to_vec()),
            )))
        }
        "evidence_edge" => {
            let groups = group_evidence_edges(rows)?;
            if groups.len() != 1 {
                return Err(invalid_contract(
                    "one evidence-edge batch must contain one edge shape",
                ));
            }
            let (group, _) = groups.into_iter().next().expect("one group");
            Ok(Some((
                compile_evidence_edge_readback(&group),
                rows_param(rows.to_vec()),
            )))
        }
        "repository_file" => Ok(Some((
            "UNWIND $rows AS row \
             OPTIONAL MATCH (r:Repository {name: row.repo, \
             project_id_normalized: row.project_id_normalized})\
             -[edge:HAS_FILE]->(f:File {id: row.id, \
             project_id_normalized: row.project_id_normalized}) \
             RETURN count(edge) AS count"
                .to_string(),
            rows_param(rows.to_vec()),
        ))),
        "call_edge" => Ok(Some((
            "UNWIND $rows AS row \
             OPTIONAL MATCH (caller:Function {id: row.caller_id, \
             project_id_normalized: row.project_id_normalized})\
             -[r:CALLS]->(callee:Function {id: row.callee_id, \
             project_id_normalized: row.project_id_normalized}) \
             WHERE r.count = row.count AND r.call_type = row.call_type \
             RETURN count(r) AS count"
                .to_string(),
            rows_param(rows.to_vec()),
        ))),
        "call_site" => Ok(Some((
            "UNWIND $rows AS row \
             OPTIONAL MATCH (caller:Function {id: row.caller_id, \
             project_id_normalized: row.project_id_normalized})\
             -[r:CALLS {site_id: row.site_id}]->\
             (callee:Function {id: row.callee_id, \
             project_id_normalized: row.project_id_normalized}) \
             RETURN count(r) AS count"
                .to_string(),
            rows_param(rows.to_vec()),
        ))),
        _ => Ok(None),
    }
}

/// `compile_endpoint_audit` — None = batch không có audit contract (không
/// phải relationships/calls phase).
fn compile_endpoint_audit(
    operation: &GraphWriteOperation,
    rows: &[Value],
) -> Result<Option<CompiledQuery>, JournalError> {
    match operation.reconciliation.as_str() {
        "typed_relationship" => {
            let groups = group_typed_relations(rows)?;
            if groups.len() != 1 {
                return Err(invalid_contract(
                    "one endpoint audit batch must contain one edge shape",
                ));
            }
            let (group, grouped_rows) = groups.into_iter().next().expect("one group");
            Ok(Some((
                compile_relationship_endpoint_audit(&group)?,
                rows_param(grouped_rows),
            )))
        }
        "repository_file" => Ok(Some((
            "UNWIND $rows AS row \
             OPTIONAL MATCH (a:Repository {name: row.repo, \
             project_id_normalized: row.project_id_normalized}) \
             WITH row, count(a) AS source_matches \
             OPTIONAL MATCH (b:File {id: row.id, \
             project_id_normalized: row.project_id_normalized}) \
             WITH row, source_matches, count(b) AS target_matches \
             WHERE source_matches <> 1 OR target_matches <> 1 \
             RETURN row.repo AS source_id, row.id AS target_id, \
             source_matches, target_matches LIMIT 20"
                .to_string(),
            rows_param(rows.to_vec()),
        ))),
        "call_edge" | "call_site" | "possible_call_site" => Ok(Some((
            "UNWIND $rows AS row \
             OPTIONAL MATCH (a:Function {id: row.caller_id, \
             project_id_normalized: row.project_id_normalized}) \
             WITH row, count(a) AS source_matches \
             OPTIONAL MATCH (b:Function {id: row.callee_id, \
             project_id_normalized: row.project_id_normalized}) \
             WITH row, source_matches, count(b) AS target_matches \
             WHERE source_matches <> 1 OR target_matches <> 1 \
             RETURN row.caller_id AS source_id, row.callee_id AS target_id, \
             source_matches, target_matches LIMIT 20"
                .to_string(),
            rows_param(rows.to_vec()),
        ))),
        "evidence_edge" => {
            let groups = group_evidence_edges(rows)?;
            if groups.len() != 1 {
                return Err(invalid_contract(
                    "one evidence audit batch must contain one edge shape",
                ));
            }
            let (group, grouped_rows) = groups.into_iter().next().expect("one group");
            Ok(Some((
                compile_evidence_endpoint_audit(&group),
                rows_param(grouped_rows),
            )))
        }
        other => {
            if matches!(operation.phase, OperationPhase::Relationships | OperationPhase::Calls)
                && matches!(other, "unsupported" | "")
            {
                return Err(invalid_contract(format!(
                    "operation {} has no endpoint audit contract",
                    operation.operation_key()
                )));
            }
            Ok(None)
        }
    }
}

// ── guard.py port: receipt transform ──────────────────────────────────────

/// `_receipt_query` — transform mutation `RETURN count(x) AS count` thành
/// WITH-count + MERGE GraphWriteReceipt (bằng chứng durable cho reconcile).
fn receipt_query(query: &str) -> Result<String, JournalError> {
    static COUNT_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let count_re = COUNT_RE.get_or_init(|| {
        regex::Regex::new(r"(?i)\bRETURN\s+count\((?P<value>\*|[A-Za-z_]\w*)\)\s+AS\s+count\s*$")
            .unwrap()
    });
    let source = query.trim().trim_end_matches(';');
    let Some(caps) = count_re.captures(source) else {
        return Err(invalid_contract(
            "journaled count mutation must end with RETURN count(value) AS count",
        ));
    };
    let value = &caps["value"];
    Ok(format!(
        "{} MERGE (receipt:GraphWriteReceipt {{id: $__journal_job_id}}) \
         SET receipt.operation_key = $__journal_operation_key, \
         receipt.row_count = __journal_count, \
         receipt.artifact_sha256 = $__journal_artifact_sha256, \
         receipt.run_id = $__journal_run_id, \
         receipt.generation = $__journal_generation, \
         receipt.applied_at = datetime() \
         RETURN __journal_count AS count",
        count_re.replace(source, format!("WITH count({value}) AS __journal_count ").as_str())
    ))
}

fn classify_error(error: &JournalError) -> RetryClass {
    match error.code.as_str() {
        "artifact_hash_mismatch" | "journal_corrupt" => RetryClass::Integrity,
        "incompatible_schema" | "invalid_contract" => RetryClass::Incompatible,
        _ => RetryClass::Terminal,
    }
}

// ── consumer drain ────────────────────────────────────────────────────────

/// `GraphWriteJournalConsumer` port. `store`/`database` đến từ GraphContext
/// (phase-02) — ladybug/falkordb đều chạy.
struct JournalConsumer<'a> {
    journal: Journal,
    run_id_value: String,
    store: &'a mut dyn GraphStore,
    database: Option<&'a str>,
    generation: String,
    lease_seconds: i64,
    #[allow(dead_code)]
    metadata: RunMetadata,
}

struct LoadedBatch {
    operation: GraphWriteOperation,
    rows: Vec<Value>,
}

impl<'a> JournalConsumer<'a> {
    /// `_load` — descriptor + artifact JSONL + count/sha256 verification.
    fn load(&self, batch: &BatchRecord) -> Result<LoadedBatch, JournalError> {
        let operation = GraphWriteOperation::from_dict(&batch.operation)
            .map_err(|error| match error.code.as_str() {
                "invalid_contract" => invalid_contract(format!(
                    "invalid persisted operation for {}: {}",
                    batch.job_id, error.message
                )),
                _ => error,
            })?;
        if operation.operation_key() != batch.operation_key {
            return Err(invalid_contract(
                "batch and descriptor operation keys do not match",
            ));
        }
        let rows = self.journal.artifacts.read_jsonl(&batch.artifact)?;
        if rows.iter().any(|row| !row.is_object()) {
            return Err(invalid_contract("artifact rows must be JSON objects"));
        }
        if rows.len() as i64 != batch.expected_count {
            return Err(JournalError::new(
                TerminalErrorCode::ArtifactHashMismatch,
                format!("artifact count does not match batch {}", batch.job_id),
            ));
        }
        Ok(LoadedBatch { operation, rows })
    }

    fn execute_query(
        &mut self,
        query: &str,
        parameters: &BTreeMap<String, Value>,
    ) -> Result<cortex_graph_writer::json_row::Row, String> {
        let records = self
            .store
            .execute_query(query, parameters, self.database)
            .map_err(|error| error.to_string())?;
        records.into_iter().next().ok_or_else(|| "query returned no records".to_string())
    }

    /// `_execute_one` — compile → receipt-transformed execute → ack/block.
    fn execute_one(&mut self, batch: &BatchRecord) -> Result<(), JournalError> {
        let fencing_token = batch.fencing_token.clone().ok_or_else(|| {
            invalid_contract(format!("batch {} leased without fencing token", batch.job_id))
        })?;
        let loaded = match self.load(batch) {
            Ok(loaded) => loaded,
            Err(error) => {
                if error.code.as_str() == "invalid_contract" {
                    self.journal.block_batch(
                        &batch.job_id,
                        &fencing_token,
                        RetryClass::Incompatible,
                        TerminalErrorCode::InvalidContract,
                    )?;
                }
                return Err(error);
            }
        };
        let (query, mut parameters) = compile_persisted_mutation(&loaded.operation, &loaded.rows)?;
        parameters.insert(
            "__journal_operation_key".to_string(),
            json!(loaded.operation.operation_key()),
        );
        let receipt = receipt_query(&query)?;
        parameters.insert("__journal_job_id".to_string(), json!(batch.job_id));
        parameters.insert(
            "__journal_artifact_sha256".to_string(),
            json!(batch.artifact.sha256),
        );
        parameters.insert("__journal_run_id".to_string(), json!(batch.run_id));
        parameters.insert("__journal_generation".to_string(), json!(self.generation));
        let record = match self.execute_query(&receipt, &parameters) {
            Ok(record) => record,
            Err(error) => {
                self.journal.mark_reconciling(
                    &batch.job_id,
                    &fencing_token,
                    Some(TerminalErrorCode::InvalidTransition),
                )?;
                return Err(invalid_contract(format!(
                    "replayed mutation for {} failed: {error}",
                    batch.job_id
                )));
            }
        };
        let count = record
            .get("count")
            .cloned()
            .ok_or_else(|| invalid_contract("receipted mutation returned no count"))?;
        let count = count
            .as_i64()
            .or_else(|| count.as_str().and_then(|s| s.parse().ok()))
            .ok_or_else(|| invalid_contract("receipted mutation returned non-integer count"))?;
        if count != batch.expected_count {
            self.journal.block_batch(
                &batch.job_id,
                &fencing_token,
                RetryClass::Integrity,
                TerminalErrorCode::InvalidContract,
            )?;
            return Err(invalid_contract(format!(
                "replayed count mismatch for {}: expected {}, received {count}",
                batch.job_id, batch.expected_count
            )));
        }
        self.journal.ack_batch(&batch.job_id, &fencing_token, None)?;
        Ok(())
    }

    /// `_reconcile_one` — receipt readback: 1 → ack, 0 → retry AMBIGUOUS,
    /// khác → block INTEGRITY.
    fn reconcile_one(&mut self, batch: &BatchRecord) -> Result<(), JournalError> {
        let fencing_token = batch.fencing_token.clone().ok_or_else(|| {
            invalid_contract(format!("batch {} leased without fencing token", batch.job_id))
        })?;
        let readback = match self.load(batch).and_then(|loaded| {
            compile_reconciliation_readback(
                &loaded.operation,
                &loaded.rows,
                Some(&batch.job_id),
                &batch.artifact.sha256,
                &batch.run_id,
                &self.generation,
            )
            .map(|compiled| (loaded, compiled))
        }) {
            Ok((_, Some(compiled))) => compiled,
            Ok((_, None)) => {
                self.journal.block_batch(
                    &batch.job_id,
                    &fencing_token,
                    RetryClass::Incompatible,
                    TerminalErrorCode::InvalidContract,
                )?;
                return Err(invalid_contract(format!(
                    "operation {} has no safe readback",
                    batch.operation_key
                )));
            }
            Err(error) => {
                let code = error.code.clone();
                self.journal.block_batch(
                    &batch.job_id,
                    &fencing_token,
                    classify_error(&error),
                    TerminalErrorCode::from_value(&code)
                        .unwrap_or(TerminalErrorCode::InvalidContract),
                )?;
                return Err(error);
            }
        };
        let (query, parameters) = readback;
        let record = match self.execute_query(&query, &parameters) {
            Ok(record) => record,
            Err(_) => {
                // Transient store errors retry; embedded/remote lỗi khác
                // block INTEGRITY như python classify (ConnectionError →
                // TRANSIENT; còn lại TERMINAL — ở đây store lỗi đã là fail).
                self.journal.schedule_retry(
                    &batch.job_id,
                    &fencing_token,
                    now_epoch_s(),
                    RetryClass::Transient,
                    TerminalErrorCode::InvalidTransition,
                )?;
                return Err(invalid_contract(format!(
                    "reconciliation readback for {} failed",
                    batch.job_id
                )));
            }
        };
        let receipt_count = record
            .get("count")
            .cloned()
            .ok_or_else(|| invalid_contract("readback returned no count"))?;
        let receipt_count = receipt_count
            .as_i64()
            .or_else(|| receipt_count.as_str().and_then(|s| s.parse().ok()))
            .ok_or_else(|| invalid_contract("readback returned non-integer count"))?;
        if receipt_count == 1 {
            self.journal.ack_batch(&batch.job_id, &fencing_token, None)?;
            return Ok(());
        }
        if receipt_count != 0 {
            self.journal.block_batch(
                &batch.job_id,
                &fencing_token,
                RetryClass::Integrity,
                TerminalErrorCode::InvalidContract,
            )?;
            return Err(invalid_contract(format!(
                "reconciliation receipt cardinality for {} must be zero or one, received {receipt_count}",
                batch.job_id
            )));
        }
        self.journal.schedule_retry(
            &batch.job_id,
            &fencing_token,
            now_epoch_s(),
            RetryClass::Ambiguous,
            TerminalErrorCode::InvalidTransition,
        )?;
        Ok(())
    }

    /// `_seal_endpoint_audit_if_ready` — node-first shape only.
    fn seal_endpoint_audit_if_ready(&mut self) -> Result<(), JournalError> {
        if self.metadata.query_shape_version != NODE_FIRST_QUERY_SHAPE {
            return Ok(());
        }
        if self.journal.endpoint_audit_status(&self.run_id_value)?.as_deref() == Some("sealed") {
            self.journal.close_barrier(&self.run_id_value, ENDPOINT_AUDIT_BARRIER)?;
            return Ok(());
        }
        let node_barrier: Option<cortex_graph_core::models::BarrierRecord> =
            self.journal.get_barrier(&self.run_id_value, NODE_PHASE_BARRIER)?;
        let drained = node_barrier.is_some_and(|barrier| barrier.status == BarrierStatus::Drained);
        if !drained {
            return Ok(());
        }
        let mut audited_rows = 0i64;
        for batch in self.journal.list_batches(&self.run_id_value)? {
            if !matches!(batch.phase, OperationPhase::Relationships | OperationPhase::Calls) {
                continue;
            }
            let loaded = self.load(&batch)?;
            let Some((query, parameters)) =
                compile_endpoint_audit(&loaded.operation, &loaded.rows)?
            else {
                continue;
            };
            let records = self
                .store
                .execute_query(&query, &parameters, self.database)
                .map_err(|error| invalid_contract(format!("endpoint audit: {error}")))?;
            if !records.is_empty() {
                return Err(invalid_contract(
                    "sealed endpoint audit found missing or ambiguous endpoints",
                ));
            }
            audited_rows += loaded.rows.len() as i64;
        }
        self.journal
            .seal_endpoint_audit(&self.run_id_value, None, None, Some(audited_rows))?;
        self.journal.close_barrier(&self.run_id_value, ENDPOINT_AUDIT_BARRIER)?;
        Ok(())
    }

    /// `drain` — claim_reconciling → reconcile | claim_batch → execute;
    /// re-seal audit trước khi kết luận hết việc.
    fn drain(&mut self) -> Result<u64, JournalError> {
        self.seal_endpoint_audit_if_ready()?;
        let mut drained = 0u64;
        loop {
            if let Some(ambiguous) =
                self.journal.claim_reconciling(Some(&self.run_id_value), self.lease_seconds)?
            {
                self.reconcile_one(&ambiguous)?;
                drained += 1;
                continue;
            }
            let pending = self
                .journal
                .claim_batch(Some(&self.run_id_value), self.lease_seconds)?;
            let pending = match pending {
                Some(batch) => batch,
                None => {
                    self.seal_endpoint_audit_if_ready()?;
                    match self.journal.claim_batch(Some(&self.run_id_value), self.lease_seconds)? {
                        Some(batch) => batch,
                        None => return Ok(drained),
                    }
                }
            };
            self.execute_one(&pending)?;
            drained += 1;
        }
    }
}

/// `_ensure_recovery_schema` — fail-closed preflight: fingerprint khớp
/// canonical + store ensure_schema verify.
fn ensure_recovery_schema(
    metadata: &RunMetadata,
    store: &mut dyn GraphStore,
    database: Option<&str>,
) -> Result<(), JournalError> {
    let expected = code_graph_schema().fingerprint();
    if metadata.schema_fingerprint != expected {
        return Err(JournalError::new(
            TerminalErrorCode::IncompatibleSchema,
            format!(
                "journal schema fingerprint is incompatible with the canonical graph schema: \
                 expected {expected}, received {}",
                metadata.schema_fingerprint
            ),
        ));
    }
    store
        .ensure_schema(database)
        .map_err(|error| invalid_contract(format!("recovery schema preflight: {error}")))?;
    Ok(())
}

/// `resume_journal` — preflight + consumer drain cho 1 journal/run.
pub fn resume_journal(
    config: &ReplayConfig,
    store: &mut dyn GraphStore,
    database: Option<&str>,
) -> Result<u64, JournalError> {
    if !config.required() || !config.path.is_file() {
        return Ok(0);
    }
    let journal = open_journal(&config.path)?;
    let run_id_value = run_id_of(&config.metadata)?;
    let run: Option<RunRecord> = journal.get_run(&run_id_value)?;
    if run.is_none() {
        return Ok(0);
    }
    ensure_recovery_schema(&config.metadata, store, database)?;
    let generation = config.metadata.generation.clone();
    let metadata = config.metadata.clone();
    journal.recover_run_leases_as_ambiguous(&run_id_value)?;
    let mut consumer = JournalConsumer {
        journal,
        run_id_value,
        store,
        database,
        generation,
        lease_seconds: config.lease_seconds,
        metadata,
    };
    consumer.drain()
}

/// drain 1 journal file theo env-config (consumer `_main`).
pub fn drain_configured(
    config: &ReplayConfig,
    store: &mut dyn GraphStore,
    database: Option<&str>,
) -> Result<u64, JournalError> {
    resume_journal(config, store, database)
}

/// Store/db context cho replay (từ GraphContext phase-02).
pub struct ReplayStoreContext {
    pub store: Box<dyn GraphStore>,
    pub database: Option<String>,
}

/// Mở store từ GraphContext cho replay.
pub fn open_replay_store(context: &GraphContext) -> Result<ReplayStoreContext, String> {
    let store = crate::graphops::open_store(context)?;
    Ok(ReplayStoreContext {
        store,
        database: Some(context.neo4j_db.clone()),
    })
}

/// `--journal-recover-only` entry (dev `journalx::recover_required_lane`
/// pre-run thay cho python consumer spawn — phase-03). Exit 0 clean /
/// 70 failure (parity consumer `_main`).
pub fn recover_only_main() -> i32 {
    let config = match replay_config_from_env() {
        Ok(Some(config)) => config,
        Ok(None) => return 0,
        Err(error) => {
            eprintln!("[journal] recovery failed: {}", error.message);
            return 70;
        }
    };
    // Store target từ env (storage overlay của dev đã set LADYBUG_PATH /
    // GRAPH_PROVIDER…); GraphContext env-shape — không cần argv.
    let provider = std::env::var("CODE_GRAPH_PROVIDER")
        .or_else(|_| std::env::var("GRAPH_PROVIDER"))
        .unwrap_or_else(|_| "falkordb".to_string())
        .to_lowercase();
    let env_lookup = |name: &str| {
        std::env::var(name).ok().filter(|v| !v.trim().is_empty())
    };
    let context = if provider == "ladybug" {
        GraphContext {
            provider: "ladybug".to_string(),
            falkordb_uri: None,
            falkordb_path: None,
            ladybug_path: Some(env_lookup("LADYBUG_PATH").unwrap_or_default()),
            falkordb_graph: env_lookup("LADYBUG_GRAPH").unwrap_or_else(|| "hyper_graph".to_string()),
            neo4j_db: env_lookup("LADYBUG_GRAPH").unwrap_or_else(|| "hyper_graph".to_string()),
        }
    } else {
        let graph = env_lookup("FALKORDB_GRAPH")
            .or_else(|| env_lookup("FALKORDB_DATABASE"))
            .unwrap_or_else(|| "hyper_graph".to_string());
        let uri = env_lookup("FALKORDB_URI");
        GraphContext {
            provider: "falkordb".to_string(),
            falkordb_uri: uri,
            falkordb_path: env_lookup("FALKORDB_PATH"),
            ladybug_path: None,
            falkordb_graph: graph.clone(),
            neo4j_db: graph,
        }
    };
    let mut replay = match open_replay_store(&context) {
        Ok(replay) => replay,
        Err(error) => {
            eprintln!("[journal] recovery failed: {error}");
            return 70;
        }
    };
    let database = replay.database.clone();
    match drain_configured(&config, replay.store.as_mut(), database.as_deref()) {
        Ok(count) => {
            if count > 0 {
                println!("[journal] autonomously recovered {count} batch(es)");
            }
            0
        }
        Err(error) => {
            eprintln!("[journal] recovery failed: {}", error.message);
            70
        }
    }
}


/// Parent-side required-lane sweep: drain mọi journal resumable trong scope
/// (`<cache>/graph-write-journal/<scope_id>/*.sqlite3`, runs Open/Draining).
/// Contract phase-01 M2: legacy-drain-only — Rust children không enqueue,
/// sweep chỉ phục hồi journal tồn tại trước run.
pub fn drain_scope_sweep(
    store: &mut dyn GraphStore,
    database: Option<&str>,
    cache_dir: &Path,
    scope_id: &str,
) -> Result<u64, JournalError> {
    let journal_dir = cache_dir.join("graph-write-journal").join(scope_id);
    let mut total = 0u64;
    let Ok(entries) = std::fs::read_dir(&journal_dir) else {
        return Ok(0);
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|ext| ext.to_str()) != Some("sqlite3") {
            continue;
        }
        let journal = open_journal(&path)?;
        for run in journal.list_runs()? {
            let resumable = matches!(run.status, cortex_graph_core::models::RunStatus::Open | cortex_graph_core::models::RunStatus::Draining);
            if !resumable {
                continue;
            }
            drop(journal);
            let config = ReplayConfig {
                path: path.clone(),
                metadata: run.metadata.clone(),
                limits: JournalLimits::default(),
                lease_seconds: 300,
            };
            total += resume_journal(&config, store, database)?;
            // Re-open journal iterator state (list_runs consumed nothing; the
            // drop above only releases the handle per resume).
            return drain_scope_sweep(store, database, cache_dir, scope_id)
                .map(|extra| total + extra);
        }
    }
    Ok(total)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Fixture metadata (run_db2b…) phải sinh đúng stored run_id — byte-compat
    /// identity với python canonical_json.
    #[test]
    fn run_id_matches_legacy_fixture_metadata() {
        let metadata_json = serde_json::json!({
            "contract_version": 1,
            "generation": "5976263d7d26_1725_8fa28a78",
            "operation_versions": {"graph-write": 1, "node-first": 1},
            "parser": "cplus",
            "parser_version": "1",
            "physical_target": "storage-target:v1:80e1e6177023de4794dfc5dad9d90b7fde1bf78dd8aa634f825bb6ce142d4cd7",
            "project_id": "cortext",
            "query_shape_version": "language-writer-node-first-v1",
            "schema_fingerprint": "8613fc08894a26c2",
            "scope_id": "0708b42ae396610a0165a5b0",
            "source_revision": "d65194dbe06b248fcb159b06c7e7f332b7e6a6e7",
            "source_snapshot": "5976263d7d26cb88d5bef3d9e1d7b54926f64fc21aa7a650c72d54c4247f7289"
        });
        let metadata: RunMetadata = serde_json::from_value(metadata_json).expect("metadata");
        let computed = run_id_of(&metadata).expect("run id");
        assert_eq!(
            computed,
            "run_db2b76b630251d5b584f0d922ec218fd0d96eb6774bde2a9e3b3b4da1efef5ea"
        );
    }
}

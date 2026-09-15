//! Message-plane graph emission (phase-05 graph leg) — port của
//! `upsert_messages_to_neo4j`, `cleanup_message_nodes_neo4j` và
//! `cleanup_all_message_nodes_neo4j` từ `tools/common/message_scan.py`.
//!
//! Query text giữ nguyên từng chữ so với Python (parity contract của
//! `cortex-graph-writer`); thực thi qua [`cortex_graph_writer::store::GraphStore`]
//! — cùng write path với language/topology writer. Store lo phần dialect
//! FalkorDB (`normalize_call_importing_subqueries`,
//! `prepare_project_scope_parameters` — đệ quy inject
//! `project_id_normalized` vào từng row trong `$rows`, `rewrite_datetime_call`
//! → `$__falkordb_now`) và param header `CYPHER` — không có write path riêng.
//!
//! Node/rel shape (khớp Python):
//! - `(Message {id})` — merge trên `id` = `msg::<sha1[:24]>`; properties:
//!   name/sender/receiver/payload/response/explanation/file_path/line/
//!   confidence/project_id/project_id_normalized/project_name/language/repo/
//!   build_system/updated_at.
//! - `(MessageEndpoint {id})` — merge trên
//!   `msg_endpoint::<project_id>::<sha1(sender|receiver)[:16]>` (sender rỗng
//!   → `"unknown"`; receiver rỗng → không có endpoint); properties:
//!   name/project_id/project_id_normalized/project_name/updated_at.
//! - `(f:File {id: file_path})-[:CONTAINS]->(m)` khi File tồn tại cùng project.
//! - `(s)-[:SENDS_MESSAGE]->(m)`, `(m)-[:TARGETS_ENDPOINT]->(r)`.
//!
//! Vector lane (Qdrant) thuộc ownership của phase-06 (cortex-embed) — xem
//! `plans/260915-analyzer-layer-rust-cutover/reports/phase05-message-scan-parity.md`
//! phần "Ownership resolution".

use std::collections::{BTreeMap, BTreeSet};

use cortex_graph_writer::json_row::{as_i64, Row};
use cortex_graph_writer::store::GraphStore;
use serde_json::{json, Map, Value};

use crate::util;

use super::record::MessageRecord;

/// Batch size mặc định của `upsert_messages_to_neo4j`.
pub const DEFAULT_MESSAGE_GRAPH_BATCH_SIZE: usize = 500;

/// Query upsert của `upsert_messages_to_neo4j` — giữ nguyên chữ Python
/// (`row.project_id_normalized` không có trong row dict; giá trị được inject
/// bởi `prepare_project_scope_parameters` ở store layer, trên CẢ 2 backend —
/// xem docstring `enrich_project_scope_value`).
pub const MESSAGE_UPSERT_QUERY: &str = r#"
    UNWIND $rows AS row
    MERGE (m:Message {id: row.id})
    SET m.name = row.name,
        m.sender = row.sender,
        m.receiver = row.receiver,
        m.payload = row.payload,
        m.response = row.response,
        m.explanation = row.explanation,
        m.file_path = row.file_path,
        m.line = row.line,
        m.confidence = row.confidence,
        m.project_id = row.project_id,
        m.project_id_normalized = row.project_id_normalized,
        m.project_name = row.project_name,
        m.language = row.language,
        m.repo = row.repo,
        m.build_system = row.build_system,
        m.updated_at = datetime()
    WITH m, row
    OPTIONAL MATCH (f:File {id: row.file_path})
    WHERE f.project_id = row.project_id
    FOREACH (_ IN CASE WHEN f IS NULL THEN [] ELSE [1] END |
      MERGE (f)-[:CONTAINS]->(m)
    )
    WITH m, row
    MERGE (s:MessageEndpoint {id: row.sender_endpoint_id})
    SET s.name = row.sender,
        s.project_id = row.project_id,
        s.project_id_normalized = row.project_id_normalized,
        s.project_name = row.project_name,
        s.updated_at = datetime()
    MERGE (s)-[:SENDS_MESSAGE]->(m)
    WITH m, row
    FOREACH (_ IN CASE WHEN row.receiver_endpoint_id = '' THEN [] ELSE [1] END |
      MERGE (r:MessageEndpoint {id: row.receiver_endpoint_id})
      SET r.name = row.receiver,
          r.project_id = row.project_id,
          r.project_id_normalized = row.project_id_normalized,
          r.project_name = row.project_name,
          r.updated_at = datetime()
      MERGE (m)-[:TARGETS_ENDPOINT]->(r)
    )
    RETURN count(m) AS count
    "#;

/// Query xoá `Message` theo file paths của `cleanup_message_nodes_neo4j`.
pub const MESSAGE_CLEANUP_BY_FILES_QUERY: &str = r#"
    WITH $project_id AS project_id, $paths AS paths
    MATCH (m:Message {project_id: project_id})
    WHERE m.file_path IN paths
    WITH collect(m) AS nodes
    UNWIND nodes AS m
    DETACH DELETE m
    RETURN count(m) AS deleted_messages
    "#;

/// Query xoá toàn bộ `Message` của project (`cleanup_all_message_nodes_neo4j`).
pub const MESSAGE_CLEANUP_ALL_QUERY: &str = r#"
    MATCH (m:Message {project_id: $project_id})
    WITH collect(m) AS nodes
    UNWIND nodes AS m
    DETACH DELETE m
    RETURN count(m) AS deleted_messages
    "#;

/// Query prune `MessageEndpoint` mồ côi (dùng chung 2 cleanup trên).
pub const MESSAGE_ENDPOINT_PRUNE_QUERY: &str = r#"
    MATCH (e:MessageEndpoint {project_id: $project_id})
    WHERE NOT (e)--()
    WITH collect(e) AS endpoints
    UNWIND endpoints AS e
    DETACH DELETE e
    RETURN count(e) AS deleted_endpoints
    "#;

/// `msg_endpoint::<project_id>::<sha1(value)[:16]>` — endpoint node identity.
pub fn message_endpoint_id(project_id: &str, value: &str) -> String {
    format!(
        "msg_endpoint::{}::{}",
        project_id,
        &util::sha1_hex(value.as_bytes())[..16]
    )
}

/// `confidence` f32 (đã round 4 decimals) → f64 khôi phục đúng giá trị thập
/// phân Python `round(confidence, 4)` — `f32→f64` thô của 0.95 là
/// 0.949999988079071, param float sẽ lệch wire so với Python `0.95`.
pub fn confidence_f64(confidence: f32) -> f64 {
    (f64::from(confidence) * 10_000.0).round() / 10_000.0
}

/// Một row của `$rows` — khớp dict Python trong `upsert_messages_to_neo4j`
/// (`sender` rỗng → `"unknown"`; `receiver` rỗng → `receiver_endpoint_id` rỗng).
pub fn message_row(
    record: &MessageRecord,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let sender = if record.sender.is_empty() {
        "unknown"
    } else {
        record.sender.as_str()
    };
    let receiver = record.receiver.as_str();
    let receiver_endpoint_id = if receiver.is_empty() {
        String::new()
    } else {
        message_endpoint_id(project_id, receiver)
    };
    let mut row = Map::new();
    row.insert("id".to_string(), json!(record.id));
    row.insert("name".to_string(), json!(record.name));
    row.insert("sender".to_string(), json!(sender));
    row.insert("receiver".to_string(), json!(receiver));
    row.insert("payload".to_string(), json!(record.payload));
    row.insert("response".to_string(), json!(record.response));
    row.insert("explanation".to_string(), json!(record.explanation));
    row.insert("file_path".to_string(), json!(record.file_path));
    row.insert("line".to_string(), json!(i64::from(record.line)));
    row.insert(
        "confidence".to_string(),
        json!(confidence_f64(record.confidence)),
    );
    row.insert("project_id".to_string(), json!(project_id));
    row.insert("project_name".to_string(), json!(project_name));
    row.insert("language".to_string(), json!(language));
    row.insert("repo".to_string(), json!(repo));
    row.insert("build_system".to_string(), json!(build_system));
    row.insert(
        "sender_endpoint_id".to_string(),
        json!(message_endpoint_id(project_id, sender)),
    );
    row.insert(
        "receiver_endpoint_id".to_string(),
        json!(receiver_endpoint_id),
    );
    row
}

fn query_count(rows: &[Row], key: &str) -> u64 {
    rows.first()
        .and_then(|row| row.get(key))
        .and_then(as_i64)
        .unwrap_or(0)
        .max(0) as u64
}

/// `upsert_messages_to_neo4j` — batch 500 rows/query, trả tổng `count`.
#[allow(clippy::too_many_arguments)]
pub fn upsert_messages_to_graph(
    store: &mut dyn GraphStore,
    database: Option<&str>,
    records: &[MessageRecord],
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Result<u64, String> {
    if records.is_empty() {
        return Ok(0);
    }
    let mut written = 0u64;
    for batch in records.chunks(DEFAULT_MESSAGE_GRAPH_BATCH_SIZE) {
        let rows: Vec<Value> = batch
            .iter()
            .map(|record| {
                Value::Object(message_row(
                    record,
                    project_id,
                    project_name,
                    language,
                    repo,
                    build_system,
                ))
            })
            .collect();
        let mut params = BTreeMap::new();
        params.insert("rows".to_string(), Value::Array(rows));
        let out = store
            .execute_query(MESSAGE_UPSERT_QUERY, &params, database)
            .map_err(|error| error.to_string())?;
        written += query_count(&out, "count");
    }
    Ok(written)
}

/// Chuẩn hoá file paths cho cleanup — `sorted({p.replace("\\","/").strip()
/// for p in paths if p.strip()})` của Python.
fn normalized_cleanup_paths(paths: &BTreeSet<String>) -> Vec<String> {
    paths
        .iter()
        .map(|path| path.replace('\\', "/").trim().to_string())
        .filter(|path| !path.is_empty())
        .collect()
}

/// `cleanup_message_nodes_neo4j` — xoá `Message` theo file_path rồi prune
/// endpoint mồ côi. Trả `(deleted_messages, deleted_endpoints)`.
pub fn cleanup_message_nodes(
    store: &mut dyn GraphStore,
    database: Option<&str>,
    project_id: &str,
    file_paths: &BTreeSet<String>,
) -> Result<(u64, u64), String> {
    let paths = normalized_cleanup_paths(file_paths);
    if paths.is_empty() {
        return Ok((0, 0));
    }
    let mut params = BTreeMap::new();
    params.insert("project_id".to_string(), json!(project_id));
    params.insert("paths".to_string(), json!(paths));
    let deleted = store
        .execute_query(MESSAGE_CLEANUP_BY_FILES_QUERY, &params, database)
        .map_err(|error| error.to_string())?;
    let deleted_messages = query_count(&deleted, "deleted_messages");
    let deleted_endpoints = prune_orphan_endpoints(store, database, project_id)?;
    Ok((deleted_messages, deleted_endpoints))
}

/// `cleanup_all_message_nodes_neo4j` — xoá mọi `Message` của project rồi prune
/// endpoint mồ côi. Trả `(deleted_messages, deleted_endpoints)`.
pub fn cleanup_all_message_nodes(
    store: &mut dyn GraphStore,
    database: Option<&str>,
    project_id: &str,
) -> Result<(u64, u64), String> {
    let mut params = BTreeMap::new();
    params.insert("project_id".to_string(), json!(project_id));
    let deleted = store
        .execute_query(MESSAGE_CLEANUP_ALL_QUERY, &params, database)
        .map_err(|error| error.to_string())?;
    let deleted_messages = query_count(&deleted, "deleted_messages");
    let deleted_endpoints = prune_orphan_endpoints(store, database, project_id)?;
    Ok((deleted_messages, deleted_endpoints))
}

fn prune_orphan_endpoints(
    store: &mut dyn GraphStore,
    database: Option<&str>,
    project_id: &str,
) -> Result<u64, String> {
    let mut params = BTreeMap::new();
    params.insert("project_id".to_string(), json!(project_id));
    let pruned = store
        .execute_query(MESSAGE_ENDPOINT_PRUNE_QUERY, &params, database)
        .map_err(|error| error.to_string())?;
    Ok(query_count(&pruned, "deleted_endpoints"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_record(sender: &str, receiver: &str) -> MessageRecord {
        MessageRecord::new(
            "proj",
            "java",
            "src/A.java",
            7,
            sender,
            receiver,
            "evt-1",
            "payload body",
            "explanation",
            0.85,
        )
    }

    #[test]
    fn endpoint_id_matches_python_hash_shape() {
        // sha1("Bus")[:16] — xác nhận bằng hashlib.sha1(b"Bus").hexdigest()[:16].
        let expected_suffix = &util::sha1_hex(b"Bus")[..16];
        assert_eq!(
            message_endpoint_id("p05", "Bus"),
            format!("msg_endpoint::p05::{expected_suffix}")
        );
        assert_eq!(message_endpoint_id("p05", "Bus").len(), "msg_endpoint::p05::".len() + 16);
    }

    #[test]
    fn message_row_sender_fallback_and_empty_receiver() {
        let record = sample_record("", "");
        let row = message_row(&record, "proj", "Phase05", "java", "/repo", "maven");
        assert_eq!(row["sender"], json!("unknown"));
        assert_eq!(row["receiver_endpoint_id"], json!(""));
        assert_eq!(row["sender_endpoint_id"], json!(message_endpoint_id("proj", "unknown")));
        assert_eq!(row["response"], Value::Null);
        assert_eq!(row["line"], json!(7));
    }

    #[test]
    fn message_row_with_receiver_builds_endpoint_id() {
        let record = sample_record("Bus", "Listener");
        let row = message_row(&record, "proj", "Phase05", "java", "/repo", "");
        assert_eq!(row["sender"], json!("Bus"));
        assert_eq!(
            row["receiver_endpoint_id"],
            json!(message_endpoint_id("proj", "Listener"))
        );
    }

    #[test]
    fn confidence_recovery_matches_python_decimal() {
        // f32(0.85) → f64 thô là 0.8500000238418579; sau recovery = 0.85.
        assert_eq!(confidence_f64(0.85_f32), 0.85);
        assert_eq!(confidence_f64(0.95_f32), 0.95);
        assert_eq!(confidence_f64(0.55_f32), 0.55);
        assert_eq!(confidence_f64(0.99_f32), 0.99);
    }

    #[test]
    fn upsert_query_shape_matches_python() {
        assert!(MESSAGE_UPSERT_QUERY.contains("MERGE (m:Message {id: row.id})"));
        assert!(MESSAGE_UPSERT_QUERY.contains("MERGE (s:MessageEndpoint {id: row.sender_endpoint_id})"));
        assert!(MESSAGE_UPSERT_QUERY.contains("(s)-[:SENDS_MESSAGE]->(m)"));
        assert!(MESSAGE_UPSERT_QUERY.contains("(m)-[:TARGETS_ENDPOINT]->(r)"));
        assert!(MESSAGE_UPSERT_QUERY.contains("MERGE (f)-[:CONTAINS]->(m)"));
        assert_eq!(MESSAGE_UPSERT_QUERY.matches("datetime()").count(), 3);
        assert!(MESSAGE_UPSERT_QUERY.trim_end().ends_with("RETURN count(m) AS count"));
        assert!(MESSAGE_CLEANUP_BY_FILES_QUERY.contains("WHERE m.file_path IN paths"));
        assert!(MESSAGE_CLEANUP_ALL_QUERY.contains("MATCH (m:Message {project_id: $project_id})"));
        assert!(MESSAGE_ENDPOINT_PRUNE_QUERY.contains("WHERE NOT (e)--()"));
    }

    #[test]
    fn normalized_cleanup_paths_sorts_and_strips() {
        let mut paths = BTreeSet::new();
        paths.insert("b.java".to_string());
        paths.insert(" a.java ".to_string());
        paths.insert(String::new());
        paths.insert("win\\path.java".to_string());
        assert_eq!(
            normalized_cleanup_paths(&paths),
            vec!["a.java", "b.java", "win/path.java"]
        );
    }
}

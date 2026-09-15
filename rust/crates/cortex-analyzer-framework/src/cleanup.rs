//! Port `tools/common/incremental_cleanup.py::cleanup_neo4j_for_files` —
//! DETACH DELETE node theo file_path/path/File.id + prune UnknownFunction mồ côi.
//! Dùng chung cho mọi analyzer Rust (python/shell/...).

use std::collections::BTreeMap;

use serde_json::Value;

use cortex_graph_writer::store::{GraphStore, StoreError};

/// Trả (deleted_nodes, deleted_unknown_functions).
pub fn cleanup_graph_files(
    store: &mut dyn GraphStore,
    project_id: &str,
    paths: &[String],
) -> Result<(i64, i64), StoreError> {
    if paths.is_empty() {
        return Ok((0, 0));
    }
    let delete_query = if store.provider() == "ladybug" {
        // Ladybug: không có label predicate `n:File` trong WHERE (dùng
        // `label(n)`), và DETACH DELETE phải chạy trực tiếp trên MATCH —
        // pattern collect/UNWIND làm mất type node ở bước binder.
        r#"
    MATCH (n)
    WHERE n.project_id = $project_id
      AND (
        coalesce(n.file_path, '') IN $paths
        OR coalesce(n.path, '') IN $paths
        OR (label(n) = 'File' AND n.id IN $paths)
      )
    DETACH DELETE n
    RETURN count(*) AS deleted_nodes
    "#
    } else {
        r#"
    WITH $paths AS paths, $project_id AS project_id
    MATCH (n)
    WHERE n.project_id = project_id
      AND (
        coalesce(n.file_path, '') IN paths
        OR coalesce(n.path, '') IN paths
        OR (n:File AND n.id IN paths)
      )
    WITH collect(DISTINCT n) AS nodes
    UNWIND nodes AS n
    WITH DISTINCT n
    DETACH DELETE n
    RETURN count(n) AS deleted_nodes
    "#
    };
    let mut params: BTreeMap<String, Value> = BTreeMap::new();
    params.insert(
        "paths".to_string(),
        Value::Array(
            paths
                .iter()
                .map(|path| Value::String(path.clone()))
                .collect(),
        ),
    );
    params.insert(
        "project_id".to_string(),
        Value::String(project_id.to_string()),
    );
    let records = store.execute_query(delete_query, &params, None)?;
    let deleted_nodes = records
        .first()
        .and_then(|record| record.get("deleted_nodes"))
        .and_then(Value::as_i64)
        .unwrap_or(0);

    if store.provider() == "ladybug" {
        // Ladybug bind chặt rel table: khi UNKNOWN_CALL chưa từng được tạo
        // (fresh store) pattern prune sẽ lỗi binder thay vì match rỗng như
        // FalkorDB. Không có rel table = không có edge = bỏ qua prune.
        let tables = store.execute_query("CALL show_tables() RETURN *", &BTreeMap::new(), None)?;
        let has_unknown_call = tables.iter().any(|record| {
            record.get("name").and_then(Value::as_str) == Some("UNKNOWN_CALL")
                && record
                    .get("type")
                    .and_then(Value::as_str)
                    .map(|t| t.eq_ignore_ascii_case("REL"))
                    .unwrap_or(false)
        });
        if !has_unknown_call {
            return Ok((deleted_nodes, 0));
        }
    }

    let prune_query = if store.provider() == "ladybug" {
        r#"
    MATCH (u:UnknownFunction)
    WHERE NOT ()-[:UNKNOWN_CALL]->(u)
    DETACH DELETE u
    RETURN count(*) AS deleted_unknown_functions
    "#
    } else {
        r#"
    MATCH (u:UnknownFunction)
    WHERE NOT ()-[:UNKNOWN_CALL]->(u)
    WITH collect(u) AS nodes
    UNWIND nodes AS u
    DETACH DELETE u
    RETURN count(u) AS deleted_unknown_functions
    "#
    };
    let records = store.execute_query(prune_query, &BTreeMap::new(), None)?;
    let deleted_unknown = records
        .first()
        .and_then(|record| record.get("deleted_unknown_functions"))
        .and_then(Value::as_i64)
        .unwrap_or(0);
    Ok((deleted_nodes, deleted_unknown))
}

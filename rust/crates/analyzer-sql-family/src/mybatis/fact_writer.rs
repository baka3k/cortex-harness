//! Port `tools/graph/writer/mybatis_writer.py` — MyBatisFactWriter trên
//! GraphStore (node/rel MERGE queries giữ nguyên từng chữ).

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};

use cortex_graph_writer::store::GraphStore;

pub const MYBATIS_NODE_LABELS: [&str; 23] = [
    "MyBatisModule",
    "MyBatisArtifact",
    "MyBatisMapper",
    "MyBatisMapperMethod",
    "MyBatisParameter",
    "MyBatisJavaProperty",
    "MyBatisXmlDocument",
    "MyBatisStatement",
    "MyBatisSqlFragment",
    "MyBatisResultMap",
    "MyBatisResultMapping",
    "MyBatisInclude",
    "MyBatisDynamicNode",
    "MyBatisConfig",
    "MyBatisSqlStatement",
    "DatabaseTable",
    "DatabaseColumn",
    "MyBatisSqlJoin",
    "MyBatisSqlParameter",
    "MyBatisSqlProvider",
    "MyBatisSpringBridge",
    "MyBatisExtension",
    "MyBatisCache",
];

const MYBATIS_RELATIONSHIP_TYPES: [&str; 18] = [
    "SEMANTIC_OF",
    "DECLARES_METHOD",
    "DECLARES_STATEMENT",
    "BINDS_STATEMENT",
    "READS_FROM",
    "WRITES_TO",
    "REFERENCES_TABLE",
    "REFERENCES_COLUMN",
    "JOINS_WITH",
    "DEPENDS_ON_PARAMETER",
    "USES_RESULT_MAP",
    "HAS_RESULT_MAPPING",
    "MAPS_PROPERTY",
    "MAPS_COLUMN",
    "NESTED_SELECT",
    "HAS_ASSOCIATION",
    "HAS_COLLECTION",
    "EXTENDS_RESULT_MAP",
];

/// `MyBatisFactWriter`.
pub struct MyBatisFactWriter<'a> {
    store: &'a mut dyn GraphStore,
    batch_size: usize,
    verbose: bool,
    schema_ready: bool,
}

impl<'a> MyBatisFactWriter<'a> {
    pub fn new(store: &'a mut dyn GraphStore, batch_size: usize, verbose: bool) -> Self {
        Self {
            store,
            batch_size: batch_size.max(1),
            verbose,
            schema_ready: false,
        }
    }

    fn ensure_schema(&mut self) -> Result<(), String> {
        if self.schema_ready {
            return Ok(());
        }
        self.store
            .ensure_schema(None)
            .map_err(|e| e.to_string())?;
        self.schema_ready = true;
        Ok(())
    }

    /// `write_fact_nodes` → written count.
    pub fn write_fact_nodes(&mut self, rows: &[Map<String, Value>]) -> Result<usize, String> {
        if rows.is_empty() {
            return Ok(0);
        }
        validate_rows(rows)?;
        self.ensure_schema()?;
        let mut total = 0usize;
        for offset in (0..rows.len()).step_by(self.batch_size) {
            let batch = &rows[offset..(offset + self.batch_size).min(rows.len())];
            validate_rows(batch)?;
            let mut by_label: BTreeMap<String, Vec<Map<String, Value>>> = BTreeMap::new();
            for row in batch {
                let label = row
                    .get("kind")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                by_label.entry(label).or_default().push(row.clone());
            }
            for (label, label_rows) in by_label {
                let query = node_query(&label);
                let mut params: BTreeMap<String, Value> = BTreeMap::new();
                params.insert(
                    "rows".to_string(),
                    Value::Array(label_rows.iter().cloned().map(Value::Object).collect()),
                );
                params.insert("updated_at".to_string(), json!(utc_now_iso()));
                let records = self
                    .store
                    .execute_query(&query, &params, None)
                    .map_err(|e| e.to_string())?;
                total += records
                    .first()
                    .and_then(|record| record.get("count"))
                    .and_then(Value::as_i64)
                    .map(|value| value.max(0) as usize)
                    .unwrap_or(label_rows.len());
            }
            if self.verbose {
                println!("[mybatis-writer] facts {}/{}", offset + batch.len(), rows.len());
            }
        }
        Ok(total)
    }

    /// `write_relationships` → written count.
    pub fn write_relationships(&mut self, rows: &[Map<String, Value>]) -> Result<usize, String> {
        if rows.is_empty() {
            return Ok(0);
        }
        validate_relationship_rows(rows)?;
        self.ensure_schema()?;
        let mut total = 0usize;
        for offset in (0..rows.len()).step_by(self.batch_size) {
            let batch = &rows[offset..(offset + self.batch_size).min(rows.len())];
            validate_relationship_rows(batch)?;
            let mut grouped: BTreeMap<(String, String, String), Vec<Map<String, Value>>> =
                BTreeMap::new();
            for row in batch {
                let key = (
                    row.get("from_label").and_then(Value::as_str).unwrap_or("").to_string(),
                    row.get("type").and_then(Value::as_str).unwrap_or("").to_string(),
                    row.get("to_label").and_then(Value::as_str).unwrap_or("").to_string(),
                );
                grouped.entry(key).or_default().push(row.clone());
            }
            for ((from_label, rel_type, to_label), rel_rows) in grouped {
                let query = relationship_query(&from_label, &rel_type, &to_label);
                let mut params: BTreeMap<String, Value> = BTreeMap::new();
                params.insert(
                    "rows".to_string(),
                    Value::Array(rel_rows.iter().cloned().map(Value::Object).collect()),
                );
                let records = self
                    .store
                    .execute_query(&query, &params, None)
                    .map_err(|e| e.to_string())?;
                let count = records
                    .first()
                    .and_then(|record| record.get("count"))
                    .and_then(Value::as_i64)
                    .unwrap_or(0);
                if count != rel_rows.len() as i64 {
                    return Err(format!(
                        "MyBatis relationship count mismatch {from_label}-[{rel_type}]->{to_label}: {count}/{}",
                        rel_rows.len()
                    ));
                }
                total += count as usize;
            }
        }
        Ok(total)
    }

    /// `cleanup_files` → deleted node count.
    pub fn cleanup_files(&mut self, project_id: &str, file_paths: &[String]) -> Result<i64, String> {
        let paths = normalize_files(file_paths);
        if paths.is_empty() {
            return Ok(0);
        }
        self.ensure_schema()?;
        let query = r#"
        MATCH (n)
        WHERE n.project_id = $project_id
          AND n.framework = 'mybatis'
          AND (
            coalesce(n.file_path, '') IN $paths
            OR coalesce(n.path, '') IN $paths
          )
        WITH collect(DISTINCT n) AS nodes
        UNWIND nodes AS n
        WITH DISTINCT n
        DETACH DELETE n
        RETURN count(n) AS deleted_nodes
        "#;
        let mut params: BTreeMap<String, Value> = BTreeMap::new();
        params.insert("project_id".to_string(), json!(project_id));
        params.insert(
            "paths".to_string(),
            Value::Array(paths.iter().map(|p| json!(p)).collect()),
        );
        let records = self
            .store
            .execute_query(query, &params, None)
            .map_err(|e| e.to_string())?;
        let deleted = records
            .first()
            .and_then(|record| record.get("deleted_nodes"))
            .and_then(Value::as_i64)
            .unwrap_or(0);
        if self.verbose {
            // mirror mybatis_writer.cleanup_files verbose print (Python).
            println!("[cleanup][graph] deleted_nodes={deleted} deleted_unknown_functions=0");
        }
        Ok(deleted)
    }
}

fn validate_rows(rows: &[Map<String, Value>]) -> Result<(), String> {
    for row in rows {
        let label = row.get("kind").and_then(Value::as_str).unwrap_or("");
        if !MYBATIS_NODE_LABELS.contains(&label) {
            return Err(format!("Unsupported MyBatis node label: {label}"));
        }
        let id_empty = row.get("id").and_then(Value::as_str).unwrap_or("").is_empty();
        let symbol_empty = row
            .get("symbol_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty();
        if id_empty || symbol_empty {
            return Err("MyBatis fact rows require both id and symbol_id".into());
        }
        if let Some(framework) = row.get("framework").and_then(Value::as_str)
            && framework != "mybatis"
        {
            return Err("MyBatis fact rows must use framework='mybatis'".into());
        }
    }
    Ok(())
}

fn validate_relationship_rows(rows: &[Map<String, Value>]) -> Result<(), String> {
    for row in rows {
        let rel_type = row.get("type").and_then(Value::as_str).unwrap_or("");
        if !MYBATIS_RELATIONSHIP_TYPES.contains(&rel_type) {
            return Err(format!("Unsupported MyBatis relationship type: {rel_type}"));
        }
        let from_label = row.get("from_label").and_then(Value::as_str).unwrap_or("");
        let to_label = row.get("to_label").and_then(Value::as_str).unwrap_or("");
        // MYBATIS_RELATIONSHIP_NODE_LABELS = labels + {Class, Function}
        let node_labels: BTreeSet<&str> = MYBATIS_NODE_LABELS
            .iter()
            .copied()
            .chain(["Class", "Function"])
            .collect();
        if !node_labels.contains(from_label) {
            return Err(format!(
                "Unsupported MyBatis relationship source label: {from_label}"
            ));
        }
        if !node_labels.contains(to_label) {
            return Err(format!(
                "Unsupported MyBatis relationship target label: {to_label}"
            ));
        }
        let from_id = row.get("from_id").and_then(Value::as_str).unwrap_or("");
        let to_id = row.get("to_id").and_then(Value::as_str).unwrap_or("");
        if from_id.is_empty() || to_id.is_empty() {
            return Err("MyBatis relationship rows require from_id and to_id".into());
        }
        let project_id = row.get("project_id").and_then(Value::as_str).unwrap_or("");
        if project_id.is_empty() {
            return Err("MyBatis relationship rows require project_id".into());
        }
    }
    Ok(())
}

fn node_query(label: &str) -> String {
    format!(
        r#"
    UNWIND $rows AS row
    MERGE (node:{label} {{id: row.id}})
    SET node += row,
        node.symbol_id = row.symbol_id,
        node.framework = 'mybatis',
        node.updated_at = $updated_at
    RETURN count(node) AS count
    "#
    )
}

fn relationship_query(from_label: &str, rel_type: &str, to_label: &str) -> String {
    format!(
        r#"
    UNWIND $rows AS row
    MATCH (a:{from_label} {{id: row.from_id}})
    MATCH (b:{to_label} {{id: row.to_id}})
    WHERE a.project_id = row.project_id
      AND b.project_id = row.project_id
    MERGE (a)-[r:{rel_type}]->(b)
    SET r += row,
        r.framework = 'mybatis'
    RETURN count(r) AS count
    "#
    )
}

fn utc_now_iso() -> String {
    // Timestamp masked trong diff — giá trị nào cũng được, format ISO.
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    format!("{:?}", now)
}

fn normalize_files(paths: &[String]) -> Vec<String> {
    let mut seen = BTreeSet::new();
    let mut ordered: Vec<String> = Vec::new();
    for path in paths {
        let normalized = path.replace('\\', "/");
        if normalized.is_empty() || seen.contains(&normalized) {
            continue;
        }
        seen.insert(normalized.clone());
        ordered.push(normalized);
    }
    ordered
}

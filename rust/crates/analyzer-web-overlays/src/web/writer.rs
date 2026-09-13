//! Port `tools/graph/writer/web_framework_writer.py` — WebFrameworkWriter qua
//! `GraphStore.execute_query` (cùng Cypher MERGE như Python).

use std::collections::BTreeMap;

use serde_json::{Map, Value};

use cortex_graph_writer::store::GraphStore;

const HANDLER_LABELS: [&str; 2] = ["Class", "Function"];

pub struct WebFrameworkWriter<'a> {
    store: &'a mut dyn GraphStore,
    database: Option<String>,
    batch_size: usize,
}

impl<'a> WebFrameworkWriter<'a> {
    pub fn new(store: &'a mut dyn GraphStore, database: Option<String>, batch_size: usize) -> Self {
        Self {
            store,
            database,
            batch_size: batch_size.max(1),
        }
    }

    /// `write_all` — trả (nodes, relationships); validate trước khi ghi.
    pub fn write_all(
        &mut self,
        node_rows: &[Map<String, Value>],
        relationship_rows: &[Map<String, Value>],
    ) -> Result<(usize, usize), String> {
        validate_nodes(node_rows)?;
        validate_relationships(relationship_rows)?;
        let nodes = self.write_nodes(node_rows)?;
        let relationships = self.write_relationships(relationship_rows)?;
        Ok((nodes, relationships))
    }

    fn write_nodes(&mut self, rows: &[Map<String, Value>]) -> Result<usize, String> {
        let mut total = 0usize;
        for offset in (0..rows.len()).step_by(self.batch_size) {
            let batch: Vec<Value> = rows[offset..(offset + self.batch_size).min(rows.len())]
                .iter()
                .map(|row| Value::Object(row.clone()))
                .collect();
            let mut params = BTreeMap::new();
            params.insert("rows".to_string(), Value::Array(batch.clone()));
            let records = self
                .store
                .execute_query(NODE_QUERY, &params, self.database.as_deref())
                .map_err(|error| error.to_string())?;
            total += count_of(&records, batch.len());
        }
        Ok(total)
    }

    fn write_relationships(&mut self, rows: &[Map<String, Value>]) -> Result<usize, String> {
        let mut total = 0usize;
        // Python: `for label in sorted(_HANDLER_LABELS)` — Class trước Function.
        for label in HANDLER_LABELS {
            let selected: Vec<Value> = rows
                .iter()
                .filter(|row| {
                    row.get("handler_label").and_then(Value::as_str) == Some(label)
                })
                .map(|row| Value::Object(row.clone()))
                .collect();
            for offset in (0..selected.len()).step_by(self.batch_size) {
                let batch = selected[offset..(offset + self.batch_size).min(selected.len())].to_vec();
                let mut params = BTreeMap::new();
                params.insert("rows".to_string(), Value::Array(batch.clone()));
                let query = relationship_query(label);
                let records = self
                    .store
                    .execute_query(&query, &params, self.database.as_deref())
                    .map_err(|error| error.to_string())?;
                total += count_of(&records, batch.len());
            }
        }
        Ok(total)
    }

    /// `delete_paths` — xoá endpoint theo (project_id, framework, file paths).
    pub fn delete_paths(
        &mut self,
        project_id: &str,
        framework: &str,
        paths: &[String],
    ) -> Result<usize, String> {
        let mut params = BTreeMap::new();
        params.insert("project_id".to_string(), Value::String(project_id.into()));
        params.insert("framework".to_string(), Value::String(framework.into()));
        params.insert(
            "paths".to_string(),
            Value::Array(paths.iter().map(|path| Value::String(path.clone())).collect()),
        );
        let records = self
            .store
            .execute_query(DELETE_PATHS_QUERY, &params, self.database.as_deref())
            .map_err(|error| error.to_string())?;
        Ok(count_of(&records, 0))
    }
}

fn count_of(records: &[cortex_graph_writer::json_row::Row], fallback: usize) -> usize {
    if let Some(value) = records
        .first()
        .and_then(|record| record.get("count"))
        .filter(|count| !count.is_null())
        .and_then(Value::as_i64)
    {
        return value.max(0) as usize;
    }
    fallback
}

fn validate_nodes(rows: &[Map<String, Value>]) -> Result<(), String> {
    for row in rows {
        for key in ["id", "project_id", "framework", "path", "http_method"] {
            let missing = match row.get(key) {
                None | Some(Value::Null) => true,
                Some(Value::String(text)) => text.is_empty(),
                Some(_) => false,
            };
            if missing {
                return Err(
                    "web endpoint row is missing identity, ownership, or route".into(),
                );
            }
        }
    }
    Ok(())
}

fn validate_relationships(rows: &[Map<String, Value>]) -> Result<(), String> {
    for row in rows {
        let is_string_nonempty = |key: &str| -> bool {
            match row.get(key) {
                Some(Value::String(text)) => !text.is_empty(),
                Some(Value::Number(_)) => true,
                _ => false,
            }
        };
        if row.get("type").and_then(Value::as_str) != Some("HANDLES")
            || !HANDLER_LABELS.contains(&row.get("handler_label").and_then(Value::as_str).unwrap_or(""))
        {
            return Err("unsupported web framework relationship".into());
        }
        for key in ["id", "endpoint_id", "project_id", "handler_name", "handler_file"] {
            if !is_string_nonempty(key) {
                return Err("web relationship row is missing identity or handler".into());
            }
        }
    }
    Ok(())
}

const NODE_QUERY: &str = "
UNWIND $rows AS row
MERGE (node:ApiEndpoint {id: row.id})
SET node += row
RETURN count(node) AS count
";

fn relationship_query(handler_label: &str) -> String {
    format!(
        r#"
    UNWIND $rows AS row
    MATCH (endpoint:ApiEndpoint {{id: row.endpoint_id, project_id: row.project_id}})
    MATCH (handler:{handler_label} {{project_id: row.project_id}})
    WHERE handler.name = row.handler_name
      AND (row.handler_file = '' OR replace(handler.file_path, '\\', '/') = row.handler_file)
      AND (row.handler_scope = '' OR coalesce(handler.scope_name, handler.class_name, '') = row.handler_scope
           OR coalesce(handler.qualified_name, '') STARTS WITH row.handler_scope + '::')
    MERGE (endpoint)-[rel:HANDLES]->(handler)
    SET rel.id = row.id,
        rel.framework = row.framework,
        rel.project_id = row.project_id,
        rel.confidence = row.confidence,
        rel.resolution_status = row.resolution_status
    MERGE (endpoint)-[:SEMANTIC_OF]->(handler)
    RETURN count(handler) AS count
    "#
    )
}

const DELETE_PATHS_QUERY: &str = "
MATCH (node:ApiEndpoint {project_id: $project_id, framework: $framework})
WHERE node.file_path IN $paths
WITH collect(node) AS nodes
FOREACH (node IN nodes | DETACH DELETE node)
RETURN size(nodes) AS count
";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn queries_match_python_source() {
        // Node query khớp `_NODE_QUERY` của Python.
        assert!(NODE_QUERY.contains("MERGE (node:ApiEndpoint {id: row.id})"));
        assert!(NODE_QUERY.contains("SET node += row"));
        // Relationship query: replace handler.file_path '\\' → '/' (Cypher
        // literal 2 ký tự backslash, khớp f-string Python).
        let query = relationship_query("Function");
        assert!(query.contains("MATCH (endpoint:ApiEndpoint {id: row.endpoint_id, project_id: row.project_id})"));
        assert!(query.contains("MATCH (handler:Function {project_id: row.project_id})"));
        assert!(query.contains("MERGE (endpoint)-[:SEMANTIC_OF]->(handler)"));
        assert!(DELETE_PATHS_QUERY.contains("RETURN size(nodes) AS count"));
    }
}

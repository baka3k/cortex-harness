//! Port `tools/graph/writer/servlet_jsp_writer.py` — ServletJspFactWriter
//! (generation-scoped nodes/relationships + analysis-state queries).

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

use cortex_graph_writer::store::GraphStore;

pub const SERVLET_JSP_NODE_LABELS: [&str; 19] = [
    "ServletJspModule",
    "WebDescriptor",
    "Servlet",
    "Filter",
    "Listener",
    "ServletMapping",
    "FilterMapping",
    "ApiEndpoint",
    "JSPView",
    "JspExpression",
    "JspTag",
    "StateSlot",
    "LifecycleEvent",
    "ErrorPage",
    "WelcomePage",
    "SecurityConstraint",
    "Authority",
    "WebTarget",
    "WebConfiguration",
];

pub const SERVLET_JSP_RELATIONSHIP_TYPES: [&str; 18] = [
    "SEMANTIC_OF",
    "DECLARES",
    "HANDLES",
    "PASSES_THROUGH",
    "FORWARDS_TO",
    "REDIRECTS_TO",
    "INCLUDES",
    "SUBMITS_TO",
    "LINKS_TO",
    "READS",
    "WRITES",
    "INITIALIZES",
    "HANDLES_LIFECYCLE",
    "PROTECTS",
    "REQUIRES_AUTHORITY",
    "RESOLVES_TO",
    "CONFIGURES",
    "MAPS_TO",
];

const EXTERNAL_ENDPOINT_LABELS: [&str; 3] = ["Class", "Function", "File"];

const COMMON_NODE_PROPERTIES: [&str; 95] = [
    "id", "semantic_id", "symbol_id", "generation_id", "name", "kind", "project_id", "project_name", "module_id",
    "language", "framework", "file_path", "start_line", "end_line", "start_column", "end_column", "confidence",
    "extraction_method", "resolution_status", "raw_value", "resolved_value", "source_symbol_id", "parser_version",
    "module_path", "evidence", "artifact_kind", "component_class", "component_name", "servlet_class",
    "servlet_name", "filter_class", "filter_name", "listener_class", "path", "raw_url_pattern", "mapping_kind",
    "http_method", "handler_names", "handler_symbol_ids", "controller_class", "declaration_sources", "scope", "key",
    "dynamic", "event_kind", "target_kind", "target", "uri", "prefix", "tag_name", "expression", "variables",
    "property_paths", "functions", "attributes", "order", "order_index", "order_status", "descriptor_order",
    "dispatcher_types", "async_supported", "metadata_complete", "namespace", "version", "doctype", "config_kind",
    "config_key", "config_value", "error_code", "exception_type", "location", "role", "methods", "method_omissions",
    "resource_collections", "transport_guarantee", "auth_method", "realm_name", "form_login_page",
    "form_error_page", "url_patterns", "servlet_names", "init_params", "load_on_startup", "jsp_file", "multipart",
    "source_files", "provenance", "coverage_status", "incomplete", "truncated", "correlation_status", "reason",
    "body", "method",
];

const RELATIONSHIP_PROPERTY_KEYS: [&str; 23] = [
    "confidence",
    "resolution_status",
    "source_file",
    "start_line",
    "end_line",
    "reason",
    "occurrence_key",
    "order_index",
    "order_status",
    "mapping_kind",
    "dispatcher_types",
    "async_supported",
    "declaration_source",
    "descriptor_order",
    "dispatch_type",
    "correlation_status",
    "provenance",
    "raw_value",
    "resolved_value",
    "methods",
    "method_omissions",
    "resource_collection",
    "resource_collection_index",
];

pub struct ServletJspFactWriter<'a> {
    store: &'a mut dyn GraphStore,
    database: Option<String>,
    batch_size: usize,
    pub verbose: bool,
}

impl<'a> ServletJspFactWriter<'a> {
    pub fn new(store: &'a mut dyn GraphStore, database: Option<String>, batch_size: usize, verbose: bool) -> Self {
        Self {
            store,
            database,
            batch_size: batch_size.max(1),
            verbose,
        }
    }

    pub fn stage_generation(
        &mut self,
        _project_id: &str,
        _module_id: &str,
        generation_id: &str,
        node_rows: Vec<Value>,
        relationship_rows: Vec<Value>,
    ) -> Result<BTreeMap<String, usize>, String> {
        let nodes_written = self.write_fact_nodes(node_rows.clone(), generation_id)?;
        let relationships_written = self.write_relationships(relationship_rows.clone(), generation_id)?;
        if nodes_written != node_rows.len() || relationships_written != relationship_rows.len() {
            return Err(format!(
                "Staged generation count mismatch: nodes={}/{} relationships={}/{}",
                nodes_written,
                node_rows.len(),
                relationships_written,
                relationship_rows.len()
            ));
        }
        let mut out = BTreeMap::new();
        out.insert("nodes".to_string(), nodes_written);
        out.insert("relationships".to_string(), relationships_written);
        Ok(out)
    }

    pub fn write_fact_nodes(&mut self, rows: Vec<Value>, generation_id: &str) -> Result<usize, String> {
        if rows.is_empty() {
            return Ok(0);
        }
        validate_node_rows(&rows, generation_id)?;
        let mut total = 0usize;
        for offset in (0..rows.len()).step_by(self.batch_size) {
            let batch = &rows[offset..(offset + self.batch_size).min(rows.len())];
            let mut by_label: BTreeMap<String, Vec<Map<String, Value>>> = BTreeMap::new();
            for row in batch {
                let kind = row.get("kind").and_then(Value::as_str).unwrap_or("").to_string();
                if let Some(map) = row.as_object() {
                    by_label.entry(kind).or_default().push(map.clone());
                }
            }
            for (label, label_rows) in &by_label {
                let mut params = BTreeMap::new();
                params.insert(
                    "rows".to_string(),
                    Value::Array(label_rows.iter().map(|row| Value::Object(row.clone())).collect()),
                );
                params.insert("updated_at".to_string(), json!(utc_now_iso()));
                let records = self
                    .store
                    .execute_query(&node_query(label), &params, self.database.as_deref())
                    .map_err(|error| error.to_string())?;
                total += result_count(&records, label_rows.len());
            }
        }
        Ok(total)
    }

    pub fn write_relationships(&mut self, rows: Vec<Value>, generation_id: &str) -> Result<usize, String> {
        if rows.is_empty() {
            return Ok(0);
        }
        validate_relationship_rows(&rows, generation_id)?;
        let mut total = 0usize;
        for offset in (0..rows.len()).step_by(self.batch_size) {
            let batch = &rows[offset..(offset + self.batch_size).min(rows.len())];
            let mut grouped: BTreeMap<(String, String, String), Vec<Map<String, Value>>> = BTreeMap::new();
            for row in batch {
                let key = (
                    row.get("from_label").and_then(Value::as_str).unwrap_or("").to_string(),
                    row.get("type").and_then(Value::as_str).unwrap_or("").to_string(),
                    row.get("to_label").and_then(Value::as_str).unwrap_or("").to_string(),
                );
                if let Some(map) = row.as_object() {
                    grouped.entry(key).or_default().push(map.clone());
                }
            }
            for ((from_label, rel_type, to_label), rel_rows) in &grouped {
                let mut params = BTreeMap::new();
                params.insert(
                    "rows".to_string(),
                    Value::Array(rel_rows.iter().map(|row| Value::Object(row.clone())).collect()),
                );
                let records = self
                    .store
                    .execute_query(
                        &relationship_query(from_label, rel_type, to_label),
                        &params,
                        self.database.as_deref(),
                    )
                    .map_err(|error| error.to_string())?;
                total += result_count(&records, rel_rows.len());
            }
        }
        Ok(total)
    }

    pub fn promote_generation(
        &mut self,
        project_id: &str,
        module_id: &str,
        generation_id: &str,
        snapshot_checksum: &str,
        coverage_status: &str,
    ) -> Result<usize, String> {
        let query = r#"
MERGE (state:ServletJspAnalysisState {id: $state_id})
SET state.project_id = $project_id,
    state.module_id = $module_id,
    state.framework = 'servlet_jsp',
    state.active_generation = $generation_id,
    state.snapshot_checksum = $snapshot_checksum,
    state.coverage_status = $coverage_status,
    state.updated_at = $updated_at
RETURN count(state) AS count
"#;
        let mut params: BTreeMap<String, Value> = BTreeMap::new();
        params.insert(
            "state_id".to_string(),
            json!(format!("servlet_jsp_state::{project_id}::{module_id}")),
        );
        params.insert("project_id".to_string(), json!(project_id));
        params.insert("module_id".to_string(), json!(module_id));
        params.insert("generation_id".to_string(), json!(generation_id));
        params.insert("snapshot_checksum".to_string(), json!(snapshot_checksum));
        params.insert("coverage_status".to_string(), json!(coverage_status));
        params.insert("updated_at".to_string(), json!(utc_now_iso()));
        let records = self
            .store
            .execute_query(query, &params, self.database.as_deref())
            .map_err(|error| error.to_string())?;
        Ok(result_count(&records, 1))
    }

    pub fn cleanup_inactive_generations(&mut self, project_id: &str, module_id: &str) -> Result<i64, String> {
        let query = r#"
MATCH (state:ServletJspAnalysisState {project_id: $project_id, module_id: $module_id})
MATCH (node)
WHERE node.project_id = $project_id
  AND node.module_id = $module_id
  AND node.framework = 'servlet_jsp'
  AND node.generation_id <> state.active_generation
WITH collect(DISTINCT node) AS nodes
UNWIND nodes AS node
DETACH DELETE node
RETURN count(node) AS deleted_nodes
"#;
        // Ladybug mất node type qua collect/UNWIND (binder) — DETACH DELETE
        // phải chạy trực tiếp trên MATCH (khớp cleanup.rs).
        let query = if self.store.provider() == "ladybug" {
            r#"
MATCH (state:ServletJspAnalysisState {project_id: $project_id, module_id: $module_id})
MATCH (node)
WHERE node.project_id = $project_id
  AND node.module_id = $module_id
  AND node.framework = 'servlet_jsp'
  AND node.generation_id <> state.active_generation
DETACH DELETE node
RETURN count(*) AS deleted_nodes
"#
        } else {
            query
        };
        let mut params: BTreeMap<String, Value> = BTreeMap::new();
        params.insert("project_id".to_string(), json!(project_id));
        params.insert("module_id".to_string(), json!(module_id));
        let records = self
            .store
            .execute_query(query, &params, self.database.as_deref())
            .map_err(|error| error.to_string())?;
        Ok(records
            .first()
            .and_then(|record| record.get("deleted_nodes"))
            .and_then(Value::as_i64)
            .unwrap_or(0))
    }

    pub fn get_active_generation(&mut self, project_id: &str, module_id: &str) -> Result<BTreeMap<String, Value>, String> {
        let query = r#"
MATCH (state:ServletJspAnalysisState {project_id: $project_id, module_id: $module_id})
RETURN state.active_generation AS active_generation,
       state.snapshot_checksum AS snapshot_checksum,
       state.coverage_status AS coverage_status
"#;
        let mut params: BTreeMap<String, Value> = BTreeMap::new();
        params.insert("project_id".to_string(), json!(project_id));
        params.insert("module_id".to_string(), json!(module_id));
        let records = self
            .store
            .execute_query(query, &params, self.database.as_deref())
            .map_err(|error| error.to_string())?;
        let mut out = BTreeMap::new();
        if let Some(record) = records.first() {
            for key in ["active_generation", "snapshot_checksum", "coverage_status"] {
                if let Some(value) = record.get(key) {
                    out.insert(key.to_string(), value.clone());
                }
            }
        }
        Ok(out)
    }

    pub fn list_active_modules(&mut self, project_id: &str) -> Result<Vec<String>, String> {
        let query = r#"
MATCH (state:ServletJspAnalysisState {project_id: $project_id})
WHERE state.framework = 'servlet_jsp'
RETURN state.module_id AS module_id
ORDER BY module_id
"#;
        let mut params: BTreeMap<String, Value> = BTreeMap::new();
        params.insert("project_id".to_string(), json!(project_id));
        let records = self
            .store
            .execute_query(query, &params, self.database.as_deref())
            .map_err(|error| error.to_string())?;
        let mut modules: Vec<String> = records
            .iter()
            .filter_map(|row| row.get("module_id").and_then(Value::as_str))
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .collect();
        modules.sort();
        modules.dedup();
        Ok(modules)
    }
}

fn result_count(records: &[Map<String, Value>], default: usize) -> usize {
    match records.first().and_then(|record| record.get("count")) {
        Some(Value::Number(number)) => number.as_i64().map(|value| value.max(0) as usize).unwrap_or(default),
        _ => default,
    }
}

fn validate_node_rows(rows: &[Value], generation_id: &str) -> Result<(), String> {
    for row in rows {
        let label = row.get("kind").and_then(Value::as_str).unwrap_or("");
        if !SERVLET_JSP_NODE_LABELS.contains(&label) {
            return Err(format!("Unsupported Servlet/JSP node label: {label}"));
        }
        for key in ["id", "semantic_id", "symbol_id", "project_id", "module_id", "framework", "generation_id"] {
            let value = row.get(key).and_then(Value::as_str).unwrap_or("");
            if value.is_empty() {
                return Err(format!("Servlet/JSP fact row is missing required ownership fields: {label}"));
            }
        }
        if row.get("framework").and_then(Value::as_str) != Some("servlet_jsp")
            || row.get("generation_id").and_then(Value::as_str) != Some(generation_id)
        {
            return Err("Servlet/JSP fact ownership or generation mismatch".to_string());
        }
        let unknown: Vec<&String> = row
            .as_object()
            .map(|map| map.keys().filter(|key| !COMMON_NODE_PROPERTIES.contains(&key.as_str())).collect())
            .unwrap_or_default();
        if !unknown.is_empty() {
            let mut sorted = unknown.clone();
            sorted.sort();
            return Err(format!("Unsupported Servlet/JSP node properties for {label}: {sorted:?}"));
        }
    }
    Ok(())
}

fn validate_relationship_rows(rows: &[Value], generation_id: &str) -> Result<(), String> {
    for row in rows {
        let rel_type = row.get("type").and_then(Value::as_str).unwrap_or("");
        if !SERVLET_JSP_RELATIONSHIP_TYPES.contains(&rel_type) {
            return Err(format!("Unsupported Servlet/JSP relationship type: {rel_type}"));
        }
        for key in ["from_label", "to_label"] {
            let label = row.get(key).and_then(Value::as_str).unwrap_or("");
            if !SERVLET_JSP_NODE_LABELS.contains(&label) && !EXTERNAL_ENDPOINT_LABELS.contains(&label) {
                return Err("Servlet/JSP relationship endpoint labels must be allowlisted".to_string());
            }
        }
        for key in ["id", "semantic_id", "from_id", "to_id", "project_id", "module_id", "generation_id"] {
            let value = row.get(key).and_then(Value::as_str).unwrap_or("");
            if value.is_empty() {
                return Err("Servlet/JSP relationship row is missing identity/ownership fields".to_string());
            }
        }
        if row.get("framework").and_then(Value::as_str) != Some("servlet_jsp")
            || row.get("generation_id").and_then(Value::as_str) != Some(generation_id)
        {
            return Err("Servlet/JSP relationship ownership or generation mismatch".to_string());
        }
        let Some(properties) = row.get("properties").and_then(Value::as_object) else {
            return Err("Servlet/JSP relationship properties must be a mapping".to_string());
        };
        let unknown: Vec<&String> = properties
            .keys()
            .filter(|key| !RELATIONSHIP_PROPERTY_KEYS.contains(&key.as_str()))
            .collect();
        if !unknown.is_empty() {
            let mut sorted = unknown.clone();
            sorted.sort();
            return Err(format!("Unsupported Servlet/JSP relationship properties: {sorted:?}"));
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
        node.updated_at = $updated_at
    RETURN count(node) AS count
    "#
    )
}

fn relationship_query(from_label: &str, rel_type: &str, to_label: &str) -> String {
    format!(
        r#"
    UNWIND $rows AS row
    MATCH (source:{from_label} {{id: row.from_id, project_id: row.project_id}})
    MATCH (target:{to_label} {{id: row.to_id, project_id: row.project_id}})
    MERGE (source)-[rel:{rel_type} {{id: row.id}}]->(target)
    SET rel += coalesce(row.properties, {{}}),
        rel.semantic_id = row.semantic_id,
        rel.project_id = row.project_id,
        rel.module_id = row.module_id,
        rel.framework = 'servlet_jsp',
        rel.generation_id = row.generation_id,
        rel.confidence = row.confidence,
        rel.resolution_status = row.resolution_status,
        rel.source_file = row.source_file,
        rel.start_line = row.start_line,
        rel.end_line = row.end_line,
        rel.reason = row.reason
    RETURN count(rel) AS count
    "#
    )
}

fn utc_now_iso() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs();
    let micros = now.subsec_micros();
    let days = secs / 86_400;
    let rem = secs % 86_400;
    let (year, month, day) = civil_from_days(days as i64);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{:06}+00:00",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60,
        micros
    )
}

fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

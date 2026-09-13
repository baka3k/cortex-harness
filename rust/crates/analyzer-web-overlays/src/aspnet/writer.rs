//! Port `tools/graph/writer/aspnet_writer.py` (AspNetFactWriter) + phần
//! `apply_graph` của `tools/common/aspnet/cli_runtime.py` — staged generation
//! writer với promote + cleanup.

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

use cortex_graph_writer::store::GraphStore;

use crate::aspnet::models::{
    AnalysisResult, ASPNET_NODE_LABELS, ASPNET_RELATIONSHIP_TYPES, EXTERNAL_LABELS,
};

fn truthy(value: Option<&String>) -> bool {
    value.map(|text| !text.is_empty()).unwrap_or(false)
}

pub struct AspNetFactWriter<'a> {
    store: &'a mut dyn GraphStore,
    database: Option<String>,
    batch_size: usize,
    pub verbose: bool,
}

impl<'a> AspNetFactWriter<'a> {
    pub fn new(
        store: &'a mut dyn GraphStore,
        database: Option<String>,
        batch_size: usize,
        verbose: bool,
    ) -> Self {
        Self {
            store,
            database,
            batch_size: batch_size.max(1),
            verbose,
        }
    }

    /// `stage_generation` — validate rồi ghi nodes + relationships, trả tổng
    /// count; mismatch → RuntimeError như Python.
    pub fn stage_generation(
        &mut self,
        project_id: &str,
        module_id: &str,
        framework: &str,
        generation_id: &str,
        node_rows: &[Map<String, Value>],
        relationship_rows: &[Map<String, Value>],
    ) -> Result<(usize, usize), String> {
        validate_scope(project_id, module_id, framework, generation_id)?;
        let nodes = self.write_nodes(node_rows, framework, generation_id)?;
        let relationships = self.write_relationships(relationship_rows, framework, generation_id)?;
        if nodes != node_rows.len() || relationships != relationship_rows.len() {
            return Err(format!(
                "ASP.NET staged count mismatch nodes={}/{} relationships={}/{}",
                nodes,
                node_rows.len(),
                relationships,
                relationship_rows.len()
            ));
        }
        Ok((nodes, relationships))
    }

    /// `write_nodes` — group theo `kind` (label) sorted trong từng batch.
    pub fn write_nodes(
        &mut self,
        rows: &[Map<String, Value>],
        framework: &str,
        generation_id: &str,
    ) -> Result<usize, String> {
        validate_nodes(rows, framework, generation_id)?;
        let mut total = 0usize;
        for offset in (0..rows.len()).step_by(self.batch_size) {
            let batch = &rows[offset..(offset + self.batch_size).min(rows.len())];
            let mut grouped: BTreeMap<String, Vec<Value>> = BTreeMap::new();
            for row in batch {
                let kind = row
                    .get("kind")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string();
                grouped
                    .entry(kind)
                    .or_default()
                    .push(Value::Object(row.clone()));
            }
            for (label, batch_rows) in &grouped {
                let mut params = BTreeMap::new();
                params.insert("rows".to_string(), Value::Array(batch_rows.clone()));
                params.insert("updated_at".to_string(), json!(utc_now_iso()));
                let records = self
                    .store
                    .execute_query(&node_query(label), &params, self.database.as_deref())
                    .map_err(|error| error.to_string())?;
                total += count_of(&records, batch_rows.len());
            }
        }
        Ok(total)
    }

    /// `write_relationships` — group theo (from_label, type, to_label) sorted.
    pub fn write_relationships(
        &mut self,
        rows: &[Map<String, Value>],
        framework: &str,
        generation_id: &str,
    ) -> Result<usize, String> {
        validate_relationships(rows, framework, generation_id)?;
        let mut total = 0usize;
        for offset in (0..rows.len()).step_by(self.batch_size) {
            let batch = &rows[offset..(offset + self.batch_size).min(rows.len())];
            let mut grouped: BTreeMap<(String, String, String), Vec<Value>> = BTreeMap::new();
            for row in batch {
                let key = (
                    row.get("from_label").and_then(Value::as_str).unwrap_or_default().to_string(),
                    row.get("type").and_then(Value::as_str).unwrap_or_default().to_string(),
                    row.get("to_label").and_then(Value::as_str).unwrap_or_default().to_string(),
                );
                grouped
                    .entry(key)
                    .or_default()
                    .push(Value::Object(row.clone()));
            }
            for ((from_label, rel_type, to_label), batch_rows) in &grouped {
                let mut params = BTreeMap::new();
                params.insert("rows".to_string(), Value::Array(batch_rows.clone()));
                let records = self
                    .store
                    .execute_query(
                        &relationship_query(from_label, rel_type, to_label),
                        &params,
                        self.database.as_deref(),
                    )
                    .map_err(|error| error.to_string())?;
                total += count_of(&records, batch_rows.len());
            }
        }
        Ok(total)
    }

    /// `promote_generation` — AspNetAnalysisState node.
    pub fn promote_generation(
        &mut self,
        project_id: &str,
        module_id: &str,
        framework: &str,
        generation_id: &str,
        snapshot_checksum: &str,
        coverage_status: &str,
    ) -> Result<usize, String> {
        validate_scope(project_id, module_id, framework, generation_id)?;
        let mut params = BTreeMap::new();
        params.insert(
            "id".to_string(),
            json!(format!("aspnet_state::{framework}::{project_id}::{module_id}")),
        );
        params.insert("project_id".to_string(), json!(project_id));
        params.insert("module_id".to_string(), json!(module_id));
        params.insert("framework".to_string(), json!(framework));
        params.insert("generation_id".to_string(), json!(generation_id));
        params.insert("snapshot_checksum".to_string(), json!(snapshot_checksum));
        params.insert("coverage_status".to_string(), json!(coverage_status));
        params.insert("updated_at".to_string(), json!(utc_now_iso()));
        let records = self
            .store
            .execute_query(PROMOTE_QUERY, &params, self.database.as_deref())
            .map_err(|error| error.to_string())?;
        Ok(count_of(&records, 1))
    }

    /// `active_state`.
    pub fn active_state(
        &mut self,
        project_id: &str,
        module_id: &str,
        framework: &str,
    ) -> Result<BTreeMap<String, String>, String> {
        let mut params = BTreeMap::new();
        params.insert("project_id".to_string(), json!(project_id));
        params.insert("module_id".to_string(), json!(module_id));
        params.insert("framework".to_string(), json!(framework));
        let records = self
            .store
            .execute_query(ACTIVE_STATE_QUERY, &params, self.database.as_deref())
            .map_err(|error| error.to_string())?;
        let mut state = BTreeMap::new();
        if let Some(record) = records.first() {
            state.insert(
                "active_generation".into(),
                record
                    .get("active_generation")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
            );
            state.insert(
                "coverage_status".into(),
                record
                    .get("coverage_status")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
            );
        }
        Ok(state)
    }

    /// `cleanup_inactive_generations`.
    pub fn cleanup_inactive_generations(
        &mut self,
        project_id: &str,
        module_id: &str,
        framework: &str,
    ) -> Result<i64, String> {
        let mut params = BTreeMap::new();
        params.insert("project_id".to_string(), json!(project_id));
        params.insert("module_id".to_string(), json!(module_id));
        params.insert("framework".to_string(), json!(framework));
        let records = self
            .store
            .execute_query(CLEANUP_QUERY, &params, self.database.as_deref())
            .map_err(|error| error.to_string())?;
        Ok(records
            .first()
            .and_then(|record| record.get("deleted_nodes"))
            .and_then(Value::as_i64)
            .unwrap_or(0))
    }
}

fn validate_scope(
    project_id: &str,
    module_id: &str,
    framework: &str,
    generation_id: &str,
) -> Result<(), String> {
    if project_id.is_empty() || module_id.is_empty() || framework.is_empty() || generation_id.is_empty() {
        return Err("project_id, module_id, framework, and generation_id are required".into());
    }
    if framework != "aspnet_core" && framework != "aspnet_framework" {
        return Err(format!("unsupported ASP.NET framework: {framework}"));
    }
    Ok(())
}

fn validate_nodes(
    rows: &[Map<String, Value>],
    framework: &str,
    generation_id: &str,
) -> Result<(), String> {
    for row in rows {
        let kind = row.get("kind").and_then(Value::as_str).unwrap_or_default();
        if !ASPNET_NODE_LABELS.contains(&kind) {
            return Err(format!("unsupported ASP.NET node label: {kind}"));
        }
        let row_framework = row.get("framework").and_then(Value::as_str).unwrap_or_default();
        let row_generation = row.get("generation_id").and_then(Value::as_str).unwrap_or_default();
        if row_framework != framework || row_generation != generation_id {
            return Err("ASP.NET node ownership/generation mismatch".into());
        }
        for key in ["id", "semantic_id", "project_id", "module_id"] {
            let value = row.get(key).and_then(Value::as_str).unwrap_or_default();
            if value.is_empty() {
                return Err("ASP.NET node is missing identity/scope".into());
            }
        }
    }
    Ok(())
}

fn validate_relationships(
    rows: &[Map<String, Value>],
    framework: &str,
    generation_id: &str,
) -> Result<(), String> {
    for row in rows {
        let rel_type = row.get("type").and_then(Value::as_str).unwrap_or_default();
        if !ASPNET_RELATIONSHIP_TYPES.contains(&rel_type) {
            return Err(format!("unsupported ASP.NET relationship type: {rel_type}"));
        }
        let from_label = row.get("from_label").and_then(Value::as_str).unwrap_or_default();
        let to_label = row.get("to_label").and_then(Value::as_str).unwrap_or_default();
        for label in [from_label, to_label] {
            if !ASPNET_NODE_LABELS.contains(&label) && !EXTERNAL_LABELS.contains(&label) {
                return Err("ASP.NET relationship labels are not allowlisted".into());
            }
        }
        let row_framework = row.get("framework").and_then(Value::as_str).unwrap_or_default();
        let row_generation = row.get("generation_id").and_then(Value::as_str).unwrap_or_default();
        if row_framework != framework || row_generation != generation_id {
            return Err("ASP.NET relationship ownership/generation mismatch".into());
        }
        for key in ["id", "semantic_id", "from_id", "to_id", "project_id", "module_id"] {
            let value = row.get(key).and_then(Value::as_str).unwrap_or_default();
            if value.is_empty() {
                return Err("ASP.NET relationship is missing identity/scope".into());
            }
        }
    }
    Ok(())
}

fn node_query(label: &str) -> String {
    format!(
        r#"
    UNWIND $rows AS row
    MERGE (node:{label} {{id: row.id}})
    SET node += row, node.updated_at = $updated_at
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
        rel.framework = row.framework,
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

const PROMOTE_QUERY: &str = "
MERGE (state:AspNetAnalysisState {id: $id})
SET state.project_id = $project_id,
    state.module_id = $module_id,
    state.framework = $framework,
    state.active_generation = $generation_id,
    state.snapshot_checksum = $snapshot_checksum,
    state.coverage_status = $coverage_status,
    state.updated_at = $updated_at
RETURN count(state) AS count
";

const ACTIVE_STATE_QUERY: &str = "
MATCH (state:AspNetAnalysisState {project_id: $project_id, module_id: $module_id, framework: $framework})
RETURN state.active_generation AS active_generation, state.coverage_status AS coverage_status
LIMIT 1
";

const CLEANUP_QUERY: &str = "
MATCH (state:AspNetAnalysisState {project_id: $project_id, module_id: $module_id, framework: $framework})
MATCH (node)
WHERE node.project_id = $project_id
  AND node.module_id = $module_id
  AND node.framework = $framework
  AND node.generation_id <> state.active_generation
WITH collect(DISTINCT node) AS nodes
UNWIND nodes AS node
DETACH DELETE node
RETURN count(node) AS deleted_nodes
";

fn count_of(records: &[cortex_graph_writer::json_row::Row], default: usize) -> usize {
    records
        .first()
        .and_then(|record| record.get("count"))
        .and_then(Value::as_i64)
        .map(|value| value.max(0) as usize)
        .unwrap_or(default)
}

/// `_utc_now` — ISO-8601 UTC microseconds; chỉ đổ vào `updated_at` (masked).
pub fn utc_now_iso() -> String {
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

/// Tổng kết `apply_graph` — stage/nodes/relationships/preserved_modules.
#[derive(Debug, Default, Clone)]
pub struct ApplyTotals {
    pub stage: String,
    pub nodes: usize,
    pub relationships: usize,
    pub preserved_modules: usize,
}

/// `cli_runtime.apply_graph` — staged write từng module với preserve-complete
/// logic. `database` là graph FalkorDB đã resolve.
pub fn apply_graph(
    store: &mut dyn GraphStore,
    result: &AnalysisResult,
    database: Option<&str>,
    batch_size: usize,
    verbose: bool,
) -> Result<ApplyTotals, String> {
    let mut totals = ApplyTotals {
        stage: "applied".into(),
        nodes: 0,
        relationships: 0,
        preserved_modules: 0,
    };
    let mut writer = AspNetFactWriter::new(store, database.map(str::to_string), batch_size, verbose);
    for module in &result.modules {
        let active_state = writer.active_state(&result.project_id, &module.module_id, &result.framework)?;
        let is_deleted_module_cleanup = module.evidence.iter().any(|item| item.ends_with(":deleted"));
        let module_coverage = result
            .module_coverage
            .get(&module.module_id)
            .cloned()
            .unwrap_or_else(|| result.coverage_status.clone());
        if module_coverage != "complete"
            && truthy(active_state.get("active_generation"))
            && active_state.get("coverage_status").map(String::as_str) == Some("complete")
            && !is_deleted_module_cleanup
        {
            totals.stage = "preserved_complete".into();
            totals.preserved_modules += 1;
            continue;
        }
        let facts: Vec<&crate::aspnet::models::SemanticFact> = result
            .facts
            .iter()
            .filter(|item| item.module_id == module.module_id)
            .collect();
        let relationships: Vec<&crate::aspnet::models::SemanticRelationship> = result
            .relationships
            .iter()
            .filter(|item| item.module_id == module.module_id)
            .collect();
        let fact_ids: Vec<String> = facts.iter().map(|item| item.stable_id.clone()).collect();
        let relationship_ids: Vec<String> =
            relationships.iter().map(|item| item.stable_id.clone()).collect();
        let checksum = crate::aspnet::models::generation_checksum(
            &fact_ids,
            &relationship_ids,
            &module_coverage,
        );
        let generation_id = crate::pyutil::stable_digest(
            &[
                crate::aspnet::models::ASPNET_MODEL_VERSION.to_string(),
                module.module_id.clone(),
                checksum.clone(),
            ],
            24,
        );
        let node_rows: Vec<Map<String, Value>> = facts
            .iter()
            .map(|item| item.to_graph_node(&generation_id))
            .collect::<Result<Vec<_>, _>>()?;
        let relationship_rows: Vec<Map<String, Value>> = relationships
            .iter()
            .map(|item| item.to_graph_row(&generation_id))
            .collect::<Result<Vec<_>, _>>()?;
        writer.stage_generation(
            &result.project_id,
            &module.module_id,
            &result.framework,
            &generation_id,
            &node_rows,
            &relationship_rows,
        )?;
        writer.promote_generation(
            &result.project_id,
            &module.module_id,
            &result.framework,
            &generation_id,
            &checksum,
            &module_coverage,
        )?;
        writer.cleanup_inactive_generations(&result.project_id, &module.module_id, &result.framework)?;
        totals.nodes += facts.len();
        totals.relationships += relationships.len();
    }
    Ok(totals)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn utc_now_shape_matches_python_isoformat() {
        let text = utc_now_iso();
        // 2026-09-14T01:23:45.123456+00:00
        assert_eq!(text.len(), 32);
        assert!(text.ends_with("+00:00"));
        assert_eq!(&text[10..11], "T");
    }
}

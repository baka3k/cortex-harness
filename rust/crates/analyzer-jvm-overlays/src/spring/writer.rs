//! Port `tools/graph/writer/spring_writer.py` — SpringFactWriter qua
//! `GraphStore.execute_query` (cùng Cypher MERGE như Python).

use std::collections::BTreeMap;

use serde_json::{json, Value};

use cortex_graph_writer::store::GraphStore;

pub const SPRING_NODE_LABELS: [&str; 26] = [
    "SpringModule",
    "SpringApplication",
    "SpringConfiguration",
    "SpringBean",
    "JpaEntity",
    "TransactionBoundary",
    "MessageDestination",
    "ScheduledTask",
    "AsyncBoundary",
    "ApplicationEvent",
    "SecurityFilterChain",
    "SecurityRule",
    "Authority",
    "Aspect",
    "Advice",
    "Pointcut",
    "ValidationConstraint",
    "CacheRegion",
    "CacheOperation",
    "ApiEndpoint",
    "Controller",
    "Service",
    "DataRepository",
    "Database",
    "Middleware",
    "MessageEndpoint",
];

pub const SPRING_RELATIONSHIP_TYPES: [&str; 39] = [
    "CONTAINS",
    "SEMANTIC_OF",
    "BOOTS_WITH",
    "IMPORTS_CONFIGURATION",
    "DEFINES_BEAN",
    "PRODUCES_BEAN",
    "INJECTS",
    "POSSIBLE_INJECTION",
    "DEPENDS_ON",
    "HANDLES",
    "USES",
    "CALLS",
    "QUERIES",
    "MANAGES_ENTITY",
    "RELATES_TO_ENTITY",
    "DERIVES_QUERY",
    "DECLARES_QUERY",
    "IMPLEMENTS_REPOSITORY",
    "APPLIES_TO",
    "CONSUMES_FROM",
    "PUBLISHES_TO",
    "HANDLED_BY",
    "RUNS",
    "PUBLISHES_EVENT",
    "LISTENS_TO",
    "EXECUTES_ASYNC",
    "HAS_SECURITY_CHAIN",
    "HAS_RULE",
    "PROTECTS",
    "REQUIRES_AUTHORITY",
    "IMPLIES",
    "DECLARES_POINTCUT",
    "APPLIES_ADVICE",
    "MATCHES_POINTCUT",
    "CONSTRAINED_BY",
    "VALIDATES_CASCADE",
    "READS_CACHE",
    "WRITES_CACHE",
    "EVICTS_CACHE",
];

fn relationship_node_labels() -> Vec<&'static str> {
    let mut labels = SPRING_NODE_LABELS.to_vec();
    labels.push("Class");
    labels.push("Function");
    labels
}

pub struct SpringFactWriter<'a> {
    store: &'a mut dyn GraphStore,
    database: Option<String>,
    batch_size: usize,
    pub verbose: bool,
    schema_ready: bool,
}

impl<'a> SpringFactWriter<'a> {
    pub fn new(store: &'a mut dyn GraphStore, database: Option<String>, batch_size: usize, verbose: bool) -> Self {
        Self {
            store,
            database,
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
            .ensure_schema(self.database.as_deref())
            .map_err(|error| error.to_string())?;
        self.schema_ready = true;
        Ok(())
    }

    pub fn write_fact_nodes(&mut self, rows: &[Value]) -> Result<usize, String> {
        if rows.is_empty() {
            return Ok(0);
        }
        validate_rows(rows)?;
        self.ensure_schema()?;
        let mut total = 0usize;
        for offset in (0..rows.len()).step_by(self.batch_size) {
            let batch = &rows[offset..(offset + self.batch_size).min(rows.len())];
            let mut by_label: BTreeMap<String, Vec<&Value>> = BTreeMap::new();
            for row in batch {
                let kind = row
                    .get("kind")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string();
                by_label.entry(kind).or_default().push(row);
            }
            for (label, label_rows) in &by_label {
                let mut params = BTreeMap::new();
                params.insert(
                    "rows".to_string(),
                    Value::Array(label_rows.iter().map(|row| (*row).clone()).collect()),
                );
                params.insert("updated_at".to_string(), json!(utc_now_iso()));
                let records = self
                    .store
                    .execute_query(&node_query(label), &params, self.database.as_deref())
                    .map_err(|error| error.to_string())?;
                total += records
                    .first()
                    .and_then(|record| record.get("count"))
                    .and_then(count_of)
                    .unwrap_or(label_rows.len());
            }
            if self.verbose {
                println!("[spring-writer] facts {}/{len}", offset + batch.len(), len = rows.len());
            }
        }
        Ok(total)
    }

    pub fn write_relationships(&mut self, rows: &[Value]) -> Result<usize, String> {
        if rows.is_empty() {
            return Ok(0);
        }
        validate_relationship_rows(rows)?;
        self.ensure_schema()?;
        let mut total = 0usize;
        for offset in (0..rows.len()).step_by(self.batch_size) {
            let batch = &rows[offset..(offset + self.batch_size).min(rows.len())];
            let mut grouped: BTreeMap<(String, String, String), Vec<&Value>> = BTreeMap::new();
            for row in batch {
                let key = (
                    str_field(row, "from_label"),
                    str_field(row, "type"),
                    str_field(row, "to_label"),
                );
                grouped.entry(key).or_default().push(row);
            }
            for ((from_label, rel_type, to_label), rel_rows) in &grouped {
                let mut params = BTreeMap::new();
                params.insert(
                    "rows".to_string(),
                    Value::Array(rel_rows.iter().map(|row| (*row).clone()).collect()),
                );
                let records = self
                    .store
                    .execute_query(
                        &relationship_query(from_label, rel_type, to_label),
                        &params,
                        self.database.as_deref(),
                    )
                    .map_err(|error| error.to_string())?;
                let count = records
                    .first()
                    .and_then(|record| record.get("count"))
                    .and_then(count_of)
                    .unwrap_or(0);
                if count != rel_rows.len() {
                    return Err(format!(
                        "Spring relationship count mismatch {from_label}-[{rel_type}]->{to_label}: {count}/{}",
                        rel_rows.len()
                    ));
                }
                total += count;
            }
        }
        Ok(total)
    }

    pub fn cleanup_files(&mut self, project_id: &str, file_paths: &[String]) -> Result<BTreeMap<String, i64>, String> {
        let paths = normalize_files(file_paths);
        if paths.is_empty() {
            let mut out = BTreeMap::new();
            out.insert("deleted_nodes".to_string(), 0);
            return Ok(out);
        }
        self.ensure_schema()?;
        let query = r#"
        MATCH (n)
        WHERE n.project_id = $project_id
          AND n.framework = 'spring'
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
        let mut params = BTreeMap::new();
        params.insert("project_id".to_string(), json!(project_id));
        params.insert("paths".to_string(), json!(paths));
        let records = self
            .store
            .execute_query(query, &params, self.database.as_deref())
            .map_err(|error| error.to_string())?;
        let deleted = records
            .first()
            .and_then(|record| record.get("deleted_nodes"))
            .and_then(count_of)
            .unwrap_or(0);
        if self.verbose {
            println!("[cleanup][graph] deleted_nodes={deleted} deleted_unknown_functions=0");
        }
        let mut out = BTreeMap::new();
        out.insert("deleted_nodes".to_string(), deleted as i64);
        Ok(out)
    }
}

fn count_of(value: &Value) -> Option<usize> {
    match value {
        Value::Number(number) => number.as_i64().map(|v| v.max(0) as usize),
        _ => None,
    }
}

fn str_field(row: &Value, key: &str) -> String {
    row.get(key).and_then(Value::as_str).unwrap_or_default().to_string()
}

fn validate_rows(rows: &[Value]) -> Result<(), String> {
    for row in rows {
        let label = str_field(row, "kind");
        if !SPRING_NODE_LABELS.contains(&label.as_str()) {
            return Err(format!("Unsupported Spring node label: {label}"));
        }
        if str_field(row, "id").is_empty() || str_field(row, "symbol_id").is_empty() {
            return Err("Spring fact rows require both id and symbol_id".to_string());
        }
    }
    Ok(())
}

fn validate_relationship_rows(rows: &[Value]) -> Result<(), String> {
    let allowed = relationship_node_labels();
    for row in rows {
        let rel_type = str_field(row, "type");
        if !SPRING_RELATIONSHIP_TYPES.contains(&rel_type.as_str()) {
            return Err(format!("Unsupported Spring relationship type: {rel_type}"));
        }
        let from_label = str_field(row, "from_label");
        let to_label = str_field(row, "to_label");
        if !allowed.contains(&from_label.as_str()) {
            return Err(format!("Unsupported Spring relationship source label: {from_label}"));
        }
        if !allowed.contains(&to_label.as_str()) {
            return Err(format!("Unsupported Spring relationship target label: {to_label}"));
        }
        if str_field(row, "from_id").is_empty() || str_field(row, "to_id").is_empty() {
            return Err("Spring relationship rows require from_id and to_id".to_string());
        }
        if str_field(row, "project_id").is_empty() {
            return Err("Spring relationship rows require project_id".to_string());
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
        node.framework = 'spring',
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
    SET r += coalesce(row.properties, {{}}),
        r.confidence = coalesce(row.confidence, row.properties.confidence, 1.0),
        r.resolution_status = coalesce(row.resolution_status, row.properties.resolution_status, 'resolved'),
        r.source_file = coalesce(row.source_file, row.properties.source_file, '')
    RETURN count(r) AS count
    "#
    )
}

fn normalize_files(paths: &[String]) -> Vec<String> {
    let mut seen = std::collections::BTreeSet::new();
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

fn utc_now_iso() -> String {
    // ISO-8601 UTC như `datetime.now(timezone.utc).isoformat()`; giá trị chỉ
    // đổ vào `updated_at` (được mask ở parity harness).
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

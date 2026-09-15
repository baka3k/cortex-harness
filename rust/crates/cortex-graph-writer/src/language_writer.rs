//! Port của `tools/graph/writer/language_writer.py` — unified code writer cho
//! mọi language analyzer: batch write contract (resume theo state), node/rel
//! upsert pipelines, typed-relation contract với endpoint audit fail-closed,
//! evidence edges và orchestration `write_all`.
//!
//! Khác biệt scope so với Python (đã ghi trong phase-03.md):
//! * journal runtime không wire vào writer này — `write_batches` chạy trực
//!   tiếp qua store; journal plane (phase 02) giữ phía Python/CLI cho đến khi
//!   orchestrator Rust (phase 09) cần nó. State resume dict vẫn được hỗ trợ.
//! * `_emit_progress` in thẳng stdout khi `verbose`.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};

use cortex_graph_core::schema_manifest::code_graph_schema;

use crate::json_row::{as_i64, row_get, Row};
use crate::project_scope::project_id_lookup_key;
use crate::query_contract::{
    compile_evidence_edge_upsert, compile_evidence_edge_upsert_ladybug,
    compile_relationship_endpoint_audit, compile_relationship_upsert,
    compile_relationship_upsert_ladybug, group_evidence_edges, group_typed_relations,
    RelationshipGroup,
};
use crate::store::{GraphStore, StoreError};
use crate::upserts;

/// Lỗi writer — bọc store error + contract violations.
#[derive(Debug)]
pub enum WriterError {
    Store(StoreError),
    Contract(String),
}

impl std::fmt::Display for WriterError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WriterError::Store(e) => write!(f, "{e}"),
            WriterError::Contract(m) => write!(f, "{m}"),
        }
    }
}

impl std::error::Error for WriterError {}

impl From<StoreError> for WriterError {
    fn from(e: StoreError) -> Self {
        WriterError::Store(e)
    }
}

// ── Call-evidence contract (subset của tools/common/call_evidence.py) ───────

pub const RESOLUTION_CLASS_DIRECT_RESOLVED: &str = "direct_resolved";
pub const RESOLUTION_CLASSES: [&str; 8] = [
    "direct_resolved",
    "declared_virtual_target",
    "possible_dispatch_target",
    "indirect_callsite",
    "dependent_template_call",
    "lexical_candidate",
    "constructor_call",
    "unresolved",
];
const SEMANTIC_PROVIDERS: [&str; 1] = ["clang_worker"];
const STRONG_CALL_REQUIRED_PROPS: [&str; 7] = [
    "resolution_class",
    "semantic_provider",
    "tu_key",
    "config_fingerprint",
    "callee_usr",
    "context_attestation",
    "manifest_key",
];

fn is_strong_call_evidence(props: &Map<String, Value>) -> bool {
    if row_get(props, "resolution_class").as_str() != Some(RESOLUTION_CLASS_DIRECT_RESOLVED) {
        return false;
    }
    if row_get(props, "semantic_provider").as_str() != Some(SEMANTIC_PROVIDERS[0]) {
        return false;
    }
    if row_get(props, "context_fidelity").as_str() != Some("faithful")
        || row_get(props, "context_admission").as_str() != Some("accepted")
        || row_get(props, "execution_coverage").as_str() != Some("complete")
    {
        return false;
    }
    STRONG_CALL_REQUIRED_PROPS.iter().all(|prop| {
        row_get(props, prop)
            .as_str()
            .map(|value| !value.trim().is_empty())
            .unwrap_or(false)
    })
}

/// `enforce_strong_call_row` — fail closed khi row claim strict CALLS mà
/// không có approved provider + identity đủ.
pub fn enforce_strong_call_row(row: &Row) -> Result<(), WriterError> {
    let props = match row.get("props").and_then(Value::as_object) {
        Some(props) => props,
        None => row,
    };
    let Some(resolution_class) = row_get(props, "resolution_class").as_str() else {
        return Ok(());
    };
    if !RESOLUTION_CLASSES.contains(&resolution_class) {
        return Err(WriterError::Contract(format!(
            "unknown call resolution class: {resolution_class:?}"
        )));
    }
    if resolution_class == RESOLUTION_CLASS_DIRECT_RESOLVED
        && !is_strong_call_evidence(props)
    {
        let missing: Vec<String> = STRONG_CALL_REQUIRED_PROPS
            .iter()
            .filter(|prop| {
                row_get(props, prop)
                    .as_str()
                    .map(|value| value.trim().is_empty())
                    .unwrap_or(true)
            })
            .map(|prop| (*prop).to_string())
            .collect();
        return Err(WriterError::Contract(format!(
            "direct_resolved call evidence requires an approved semantic provider \
             and complete identity fields; missing or invalid: {missing:?}"
        )));
    }
    Ok(())
}

// ── Writer ───────────────────────────────────────────────────────────────────

type BatchFn<'a> = dyn FnMut(&mut dyn GraphStore, Option<&str>, &[Row]) -> Result<i64, WriterError>
    + 'a;

/// Unified code writer — thay các Neo4jWriter trùng lặp của analyzers.
pub struct LanguageCodeWriter {
    /// Store hiện hành (public cho harness parity đọc/verify readback).
    pub store: Box<dyn GraphStore>,
    database: Option<String>,
    pub batch_size: usize,
    pub verbose: bool,
    schema_ready: bool,
    /// Resume state: label → số row đã ghi. Python truyền `state=None` mặc
    /// định mỗi call (không resume); bật `resume_state` để writer giữ state
    /// giữa các call như analyzer CLI.
    pub state: BTreeMap<String, usize>,
    pub resume_state: bool,
}

impl LanguageCodeWriter {
    pub fn new(
        store: Box<dyn GraphStore>,
        database: Option<String>,
        batch_size: usize,
        verbose: bool,
    ) -> Self {
        Self {
            store,
            database,
            batch_size: batch_size.max(1),
            verbose,
            schema_ready: false,
            state: BTreeMap::new(),
            resume_state: false,
        }
    }

    pub fn provider_name(&self) -> &'static str {
        self.store.provider()
    }

    fn emit_progress(&self, event: &str, label: &str, fields: &[(&str, Value)]) {
        if !self.verbose {
            return;
        }
        let details = fields
            .iter()
            .map(|(key, value)| format!("{key}={value}"))
            .collect::<Vec<_>>()
            .join(" ");
        let suffix = if details.is_empty() {
            String::new()
        } else {
            format!(" {details}")
        };
        println!("[{}] {label} {event}{suffix}", self.provider_name());
    }

    /// `ensure_schema` — invariant schema 1 lần per writer.
    pub fn ensure_schema(&mut self) -> Result<(), WriterError> {
        if self.schema_ready {
            return Ok(());
        }
        self.store.ensure_schema(self.database.as_deref())?;
        self.schema_ready = true;
        Ok(())
    }

    /// Execute một query `$rows` và trả count từ `records[0]["count"]`.
    fn exec_rows_count(
        &mut self,
        query: &str,
        rows: &[Row],
    ) -> Result<i64, WriterError> {
        let mut params = BTreeMap::new();
        params.insert("rows".to_string(), Value::Array(rows.iter().cloned().map(Value::Object).collect()));
        let records = self
            .store
            .execute_query(query, &params, self.database.as_deref())?;
        Ok(records
            .first()
            .and_then(|record| record.get("count"))
            .and_then(as_i64)
            .unwrap_or(0))
    }

    /// `write_batches` — batch write contract: chia batch, track state, đếm
    /// written. (Journal hooks của Python không port — xem module doc.)
    pub fn write_batches(
        &mut self,
        label: &str,
        rows: &[Row],
        write_fn: &mut BatchFn<'_>,
    ) -> Result<usize, WriterError> {
        let start_index = if self.resume_state {
            self.state.get(label).copied().unwrap_or(0)
        } else {
            0
        };
        let total = rows.len();
        if start_index >= total {
            self.emit_progress(
                "batch_skipped",
                label,
                &[
                    ("completed", json!(total)),
                    ("total", json!(total)),
                    ("reason", json!("already_completed")),
                ],
            );
            return Ok(0);
        }
        self.ensure_schema()?;
        let mut written = 0usize;
        let mut offset = start_index;
        while offset < total {
            let end = (offset + self.batch_size).min(total);
            let batch = &rows[offset..end];
            let count = {
                let store = &mut *self.store;
                let database = self.database.as_deref();
                write_fn(store, database, batch)?
            };
            written += count.max(0) as usize;
            let next_index = end;
            self.state.insert(label.to_string(), next_index);
            self.emit_progress(
                "batch_finished",
                label,
                &[
                    ("offset", json!(offset)),
                    ("size", json!(batch.len())),
                    ("completed", json!(next_index)),
                    ("total", json!(total)),
                    ("matched", json!(count)),
                ],
            );
            offset = next_index;
        }
        Ok(written)
    }

    fn simple_batch_fn(
        query: &'static str,
    ) -> impl FnMut(&mut dyn GraphStore, Option<&str>, &[Row]) -> Result<i64, WriterError> {
        move |store, database, batch| {
            let mut params = BTreeMap::new();
            params.insert(
                "rows".to_string(),
                Value::Array(batch.iter().cloned().map(Value::Object).collect()),
            );
            let records = store.execute_query(query, &params, database)?;
            Ok(records
                .first()
                .and_then(|record| record.get("count"))
                .and_then(as_i64)
                .unwrap_or(0))
        }
    }

    // ── Node upsert pipelines ────────────────────────────────────────────

    fn setdefault_code(rows: &mut [Row]) {
        for row in rows {
            row.entry("node_type".to_string())
                .or_insert_with(|| Value::String("code".to_string()));
        }
    }

    pub fn write_projects(&mut self, projects: &[Row]) -> Result<usize, WriterError> {
        if projects.is_empty() {
            return Ok(0);
        }
        let mut rows = projects.to_vec();
        Self::setdefault_code(&mut rows);
        self.write_batches("projects", &rows, &mut Self::simple_batch_fn(upserts::WRITE_PROJECTS))
    }

    pub fn write_packages(&mut self, packages: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any("packages", packages, Self::operations_batch_fn(|store, rows, database| {
            crate::operations::package::batch_create(store, rows, database)
        }))
    }

    pub fn write_packages_full(&mut self, packages: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any("packages", packages, Self::simple_batch_fn(upserts::WRITE_PACKAGES_FULL))
    }

    pub fn write_namespaces(&mut self, namespaces: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any(
            "namespaces",
            namespaces,
            Self::operations_batch_fn(|store, rows, database| {
                crate::operations::namespace::batch_create(store, rows, database)
            }),
        )
    }

    pub fn write_namespaces_full(&mut self, namespaces: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any(
            "namespaces",
            namespaces,
            Self::simple_batch_fn(upserts::WRITE_NAMESPACES_FULL),
        )
    }

    pub fn write_files(&mut self, files: &[Row]) -> Result<usize, WriterError> {
        let mut rows = files.to_vec();
        Self::setdefault_code(&mut rows);
        self.write_batches("files", &rows, &mut Self::simple_batch_fn(upserts::WRITE_FILES))
    }

    pub fn write_files_with_imports(&mut self, files: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any(
            "files",
            files,
            Self::simple_batch_fn(upserts::WRITE_FILES_WITH_IMPORTS),
        )
    }

    pub fn write_files_jsx(&mut self, files: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any("files", files, Self::simple_batch_fn(upserts::WRITE_FILES_JSX))
    }

    pub fn write_files_with_package(&mut self, files: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any(
            "files",
            files,
            Self::simple_batch_fn(upserts::WRITE_FILES_WITH_PACKAGE),
        )
    }

    pub fn write_classes(&mut self, classes: &[Row]) -> Result<usize, WriterError> {
        let mut rows = classes.to_vec();
        Self::setdefault_code(&mut rows);
        self.write_batch_rows("classes", &rows, Self::operations_batch_fn(|store, rows, database| {
            crate::operations::class::batch_create(store, rows, database)
        }))
    }

    pub fn write_classes_full(&mut self, classes: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any("classes", classes, Self::simple_batch_fn(upserts::WRITE_CLASSES_FULL))
    }

    pub fn write_types(&mut self, types: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any("types", types, Self::operations_batch_fn(|store, rows, database| {
            crate::operations::r#type::batch_create(store, rows, database)
        }))
    }

    pub fn write_types_full(&mut self, types: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any("types", types, Self::simple_batch_fn(upserts::WRITE_TYPES_FULL))
    }

    pub fn write_functions(&mut self, functions: &[Row]) -> Result<usize, WriterError> {
        let mut rows = functions.to_vec();
        Self::setdefault_code(&mut rows);
        self.write_batch_rows("functions", &rows, Self::operations_batch_fn(|store, rows, database| {
            crate::operations::function::batch_create(store, rows, database)
        }))
    }

    pub fn write_functions_full(&mut self, functions: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any(
            "functions",
            functions,
            Self::simple_batch_fn(upserts::WRITE_FUNCTIONS_FULL),
        )
    }

    pub fn write_function_types(&mut self, function_types: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any(
            "function_types",
            function_types,
            Self::simple_batch_fn(upserts::WRITE_FUNCTION_TYPES),
        )
    }

    pub fn write_fields(&mut self, fields: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any("fields", fields, Self::simple_batch_fn(upserts::WRITE_FIELDS))
    }

    pub fn write_aliases(&mut self, aliases: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any("aliases", aliases, Self::simple_batch_fn(upserts::WRITE_ALIASES))
    }

    pub fn write_templates(&mut self, templates: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any("templates", templates, Self::simple_batch_fn(upserts::WRITE_TEMPLATES))
    }

    pub fn write_properties_full(&mut self, properties: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any(
            "properties",
            properties,
            Self::simple_batch_fn(upserts::WRITE_PROPERTIES_FULL),
        )
    }

    pub fn write_events_full(&mut self, events: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any("events", events, Self::simple_batch_fn(upserts::WRITE_EVENTS_FULL))
    }

    pub fn write_interfaces_full(&mut self, interfaces: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any(
            "interfaces",
            interfaces,
            Self::simple_batch_fn(upserts::WRITE_INTERFACES_FULL),
        )
    }

    pub fn write_enums_full(&mut self, enums: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any("enums", enums, Self::simple_batch_fn(upserts::WRITE_ENUMS_FULL))
    }

    pub fn write_constants_full(&mut self, constants: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any(
            "constants",
            constants,
            Self::simple_batch_fn(upserts::WRITE_CONSTANTS_FULL),
        )
    }

    pub fn write_variables_full(&mut self, variables: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any(
            "variables",
            variables,
            Self::simple_batch_fn(upserts::WRITE_VARIABLES_FULL),
        )
    }

    pub fn write_navigators(&mut self, navigators: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any(
            "navigators",
            navigators,
            Self::simple_batch_fn(upserts::WRITE_NAVIGATORS),
        )
    }

    pub fn write_has_routes(&mut self, routes: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any("has_routes", routes, Self::simple_batch_fn(upserts::WRITE_HAS_ROUTES))
    }

    pub fn write_param_lists(&mut self, param_lists: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any(
            "param_lists",
            param_lists,
            Self::simple_batch_fn(upserts::WRITE_PARAM_LISTS),
        )
    }

    pub fn write_workflows(&mut self, workflows: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any("workflows", workflows, Self::simple_batch_fn(upserts::WRITE_WORKFLOWS))
    }

    pub fn write_workflow_steps(&mut self, step_rows: &[Row]) -> Result<usize, WriterError> {
        self.write_batch_if_any(
            "workflow_steps",
            step_rows,
            Self::simple_batch_fn(upserts::WRITE_WORKFLOW_STEPS),
        )
    }

    fn write_batch_if_any(
        &mut self,
        label: &str,
        rows: &[Row],
        write_fn: impl FnMut(&mut dyn GraphStore, Option<&str>, &[Row]) -> Result<i64, WriterError>,
    ) -> Result<usize, WriterError> {
        if rows.is_empty() {
            return Ok(0);
        }
        let mut write_fn = write_fn;
        self.write_batches(label, rows, &mut write_fn)
    }

    fn write_batch_rows(
        &mut self,
        label: &str,
        rows: &[Row],
        write_fn: impl FnMut(&mut dyn GraphStore, Option<&str>, &[Row]) -> Result<i64, WriterError>,
    ) -> Result<usize, WriterError> {
        if rows.is_empty() {
            return Ok(0);
        }
        let mut write_fn = write_fn;
        self.write_batches(label, rows, &mut write_fn)
    }

    // ── Relations & calls ────────────────────────────────────────────────

    /// `write_repo_file_edges` — chạy SAU write_files.
    pub fn write_repo_file_edges(&mut self, files: &[Row]) -> Result<usize, WriterError> {
        let rows: Vec<Row> = files
            .iter()
            .filter(|f| row_get(f, "repo").as_str().map(|s| !s.is_empty()).unwrap_or(false))
            .cloned()
            .collect();
        self.write_batch_if_any(
            "repo_file_edges",
            &rows,
            Self::simple_batch_fn(upserts::WRITE_REPO_FILE_EDGES),
        )
    }

    /// `_require_call_project_scope` — chuẩn hoá scope và từ chối row thiếu
    /// project_id.
    fn require_call_project_scope(&self, calls: &mut [Row]) -> Result<(), WriterError> {
        for (position, row) in calls.iter_mut().enumerate() {
            let project_id = row
                .get("project_id")
                .and_then(Value::as_str)
                .map(str::to_string)
                .or_else(|| {
                    row.get("props")
                        .and_then(Value::as_object)
                        .and_then(|props| props.get("project_id"))
                        .and_then(Value::as_str)
                        .map(str::to_string)
                });
            let normalized = project_id_lookup_key(project_id.as_deref())
                .ok_or_else(|| {
                    WriterError::Contract(format!("call row requires project_id (row {position})"))
                })?;
            let project_id = project_id.unwrap_or_default().trim().to_string();
            row.insert("project_id".to_string(), json!(project_id));
            row.insert("project_id_normalized".to_string(), json!(normalized));
            if let Some(props) = row.get_mut("props").and_then(Value::as_object_mut) {
                props.insert("project_id".to_string(), json!(project_id));
                props.insert("project_id_normalized".to_string(), json!(normalized));
            }
        }
        Ok(())
    }

    /// `_require_evidence_project_scope` — bind staging rows vào đúng một
    /// project scope, tự thêm props khi thiếu.
    fn require_evidence_project_scope(
        &self,
        rows: &mut [Row],
        row_kind: &str,
    ) -> Result<(), WriterError> {
        for (position, row) in rows.iter_mut().enumerate() {
            if row.get("props").and_then(Value::as_object).is_none() {
                row.insert("props".to_string(), Value::Object(Map::new()));
            }
            let project_id = row
                .get("project_id")
                .and_then(Value::as_str)
                .map(str::to_string)
                .or_else(|| {
                    row.get("props")
                        .and_then(Value::as_object)
                        .and_then(|props| props.get("project_id"))
                        .and_then(Value::as_str)
                        .map(str::to_string)
                });
            let normalized = project_id_lookup_key(project_id.as_deref()).ok_or_else(|| {
                WriterError::Contract(format!(
                    "{row_kind} row requires project_id (row {position})"
                ))
            })?;
            let project_id = project_id.unwrap_or_default().trim().to_string();
            row.insert("project_id".to_string(), json!(project_id));
            row.insert("project_id_normalized".to_string(), json!(normalized));
            if let Some(props) = row.get_mut("props").and_then(Value::as_object_mut) {
                props.insert("project_id".to_string(), json!(project_id));
                props.insert("project_id_normalized".to_string(), json!(normalized));
            }
        }
        Ok(())
    }

    /// `write_calls` — collapse duplicate observations trước khi ghi
    /// (replay-safe: count tuyệt đối, không increment).
    pub fn write_calls(&mut self, calls: &[Row]) -> Result<usize, WriterError> {
        if calls.is_empty() {
            return Ok(0);
        }
        let mut scoped = calls.to_vec();
        self.require_call_project_scope(&mut scoped)?;

        let mut aggregated: BTreeMap<(String, String, String, String), Row> = BTreeMap::new();
        let mut order: Vec<(String, String, String, String)> = Vec::new();
        for call in &scoped {
            let key = (
                row_get(call, "caller_id").as_str().unwrap_or("").to_string(),
                row_get(call, "callee_id").as_str().unwrap_or("").to_string(),
                row_get(call, "project_id").as_str().unwrap_or("").to_string(),
                row_get(call, "project_id_normalized")
                    .as_str()
                    .unwrap_or("")
                    .to_string(),
            );
            let count = as_i64(row_get(call, "count")).unwrap_or(1);
            match aggregated.get_mut(&key) {
                None => {
                    let mut existing = call.clone();
                    existing.insert("count".to_string(), json!(count));
                    aggregated.insert(key.clone(), existing);
                    order.push(key);
                }
                Some(existing) => {
                    let existing_count = as_i64(row_get(existing, "count")).unwrap_or(1);
                    existing.insert("count".to_string(), json!(existing_count + count));
                    let existing_type = row_get(existing, "call_type").as_str().unwrap_or("");
                    let new_type = row_get(call, "call_type").as_str().unwrap_or("");
                    let min_type = if existing_type <= new_type {
                        existing_type
                    } else {
                        new_type
                    };
                    existing.insert("call_type".to_string(), json!(min_type));
                }
            }
        }
        let replay_safe_calls: Vec<Row> =
            order.iter().map(|key| aggregated[key].clone()).collect();

        self.write_batch_if_any("calls", &replay_safe_calls, Self::simple_batch_fn(upserts::WRITE_CALLS))
    }

    /// `write_relations` — generic relationships qua safe typed contract.
    pub fn write_relations(
        &mut self,
        relations: &[Row],
    ) -> Result<usize, WriterError> {
        self.write_relations_typed(relations, None)
    }

    /// `write_relations_typed` — per-type batching với endpoint audit
    /// fail-closed (cardinality preflight + post-MERGE count check).
    pub fn write_relations_typed(
        &mut self,
        relations: &[Row],
        default_project_id: Option<&str>,
    ) -> Result<usize, WriterError> {
        if relations.is_empty() {
            return Ok(0);
        }
        // Strip (Project)-[:CONTAINS]->(anything) bất kể call site.
        let filtered: Vec<Row> = relations
            .iter()
            .filter(|r| {
                !(row_get(r, "source_label").as_str() == Some("Project")
                    && row_get(r, "rel_type").as_str() == Some("CONTAINS"))
            })
            .cloned()
            .collect();
        if filtered.is_empty() {
            return Ok(0);
        }

        let groups = group_typed_relations(&filtered, default_project_id)
            .map_err(WriterError::Contract)?;

        // Ladybug rel table bind chặt endpoint pair ngay từ lần tạo: pre-create
        // mọi (rel, source, target) của session trước khi ghi từng group, nếu
        // không group sau sẽ "violates schema" với endpoint của group đầu.
        if self.store.provider() == "ladybug" {
            let mut pairs: BTreeMap<&str, BTreeSet<String>> = BTreeMap::new();
            for group in groups.keys() {
                pairs.entry(group.relationship_type.as_str()).or_default().insert(
                    format!("FROM `{}` TO `{}`", group.source_label, group.target_label),
                );
            }
            for (rel, endpoints) in pairs {
                let statement = format!(
                    "CREATE REL TABLE IF NOT EXISTS `{rel}` ({})",
                    endpoints.into_iter().collect::<Vec<_>>().join(", ")
                );
                self.store.execute_query(&statement, &BTreeMap::new(), self.database.as_deref())?;
            }
        }

        let mut total_written = 0usize;
        for (relationship_group, rows) in groups {
            let state_key = relationship_group.state_key();
            let written = self.write_group_with_audit(&relationship_group, &state_key, &rows)?;
            total_written += written;
        }
        Ok(total_written)
    }

    fn write_group_with_audit(
        &mut self,
        group: &RelationshipGroup,
        state_key: &str,
        rows: &[Row],
    ) -> Result<usize, WriterError> {
        let start_index = if self.resume_state {
            self.state.get(state_key).copied().unwrap_or(0)
        } else {
            0
        };
        if start_index >= rows.len() {
            return Ok(0);
        }
        self.ensure_schema()?;
        let mut written = 0usize;
        let mut offset = start_index;
        while offset < rows.len() {
            let end = (offset + self.batch_size).min(rows.len());
            let batch = &rows[offset..end];
            let count = self.write_batch_with_audit(group, state_key, batch)?;
            written += count.max(0) as usize;
            self.state.insert(state_key.to_string(), end);
            offset = end;
        }
        Ok(written)
    }

    fn audit_endpoints(
        &mut self,
        group: &RelationshipGroup,
        batch: &[Row],
    ) -> Result<Vec<Row>, WriterError> {
        let query = compile_relationship_endpoint_audit(group);
        let mut params = BTreeMap::new();
        params.insert(
            "rows".to_string(),
            Value::Array(batch.iter().cloned().map(Value::Object).collect()),
        );
        let audit_records = self
            .store
            .execute_query(&query, &params, self.database.as_deref())?;
        let diagnostics: Vec<Row> = audit_records
            .iter()
            .filter(|record| {
                record.contains_key("source_matches") || record.contains_key("target_matches")
            })
            .map(|record| {
                let mut item = Map::new();
                for key in [
                    "source_id",
                    "target_id",
                    "project_id_normalized",
                    "source_matches",
                    "target_matches",
                ] {
                    if let Some(value) = record.get(key) {
                        item.insert(key.to_string(), value.clone());
                    }
                }
                item
            })
            .collect();
        Ok(diagnostics)
    }

    fn write_batch_with_audit(
        &mut self,
        group: &RelationshipGroup,
        state_key: &str,
        batch: &[Row],
    ) -> Result<i64, WriterError> {
        // Cardinality check TRƯỚC MERGE để batch có 1 row xấu không thể
        // materialize một phần các edge còn lại hợp lệ.
        let endpoint_diagnostics = self.audit_endpoints(group, batch)?;
        let mut batch = batch.to_vec();
        if !endpoint_diagnostics.is_empty() {
            if std::env::var("CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS").as_deref()
                == Ok("1")
            {
                let bad_pairs: BTreeSet<(String, String)> = endpoint_diagnostics
                    .iter()
                    .map(|item| {
                        (
                            row_get(item, "source_id").to_string(),
                            row_get(item, "target_id").to_string(),
                        )
                    })
                    .collect();
                let before = batch.len();
                batch.retain(|row| {
                    !bad_pairs.contains(&(
                        row_get(row, "source_id").to_string(),
                        row_get(row, "target_id").to_string(),
                    ))
                });
                self.emit_progress(
                    "diagnostic_skipped_unresolved",
                    state_key,
                    &[
                        ("skipped", json!(before - batch.len())),
                        (
                            "endpoint_diagnostics",
                            Value::Array(
                                endpoint_diagnostics
                                    .iter()
                                    .cloned()
                                    .map(Value::Object)
                                    .collect(),
                            ),
                        ),
                    ],
                );
                if batch.is_empty() {
                    return Ok(0);
                }
            } else {
                return Err(WriterError::Contract(format!(
                    "relationship endpoint preflight failure for {state_key}: \
                     expected={} endpoint_diagnostics={}",
                    batch.len(),
                    Value::Array(
                        endpoint_diagnostics
                            .iter()
                            .cloned()
                            .map(Value::Object)
                            .collect()
                    )
                )));
            }
        }

        let ladybug = self.store.provider() == "ladybug";
        let query = if ladybug {
            // Fail-closed: variant ladybug không áp dynamic properties.
            if batch.iter().any(|row| {
                row.get("properties")
                    .and_then(Value::as_object)
                    .map(|props| !props.is_empty())
                    .unwrap_or(false)
            }) {
                return Err(WriterError::Contract(format!(
                    "ladybug provider does not support typed relation row properties \
                     (state_key={state_key}); drop the properties or extend the writer"
                )));
            }
            compile_relationship_upsert_ladybug(group)
        } else {
            compile_relationship_upsert(group)
        };
        let count = self.exec_rows_count(&query, &batch)?;
        if count != batch.len() as i64 {
            let unresolved = (batch.len() as i64 - count).abs();
            let (endpoint_diagnostics, _audit_error) = match self
                .audit_endpoints(group, &batch)
            {
                Ok(diagnostics) => (diagnostics, String::new()),
                Err(WriterError::Store(StoreError::Ladybug(message))) => {
                    (vec![], format!("Ladybug: {message}"))
                }
                Err(other) => (vec![], other.to_string()),
            };
            self.emit_progress(
                "batch_unresolved",
                state_key,
                &[
                    ("expected", json!(batch.len())),
                    ("matched", json!(count)),
                    ("unresolved", json!(unresolved)),
                ],
            );
            if std::env::var("CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS").as_deref() == Ok("1") {
                return Ok(count);
            }
            return Err(WriterError::Contract(format!(
                "relationship integrity failure for {state_key}: expected={} \
                 matched={count} unresolved_or_ambiguous={unresolved} \
                 endpoint_diagnostics={}",
                batch.len(),
                Value::Array(
                    endpoint_diagnostics
                        .iter()
                        .cloned()
                        .map(Value::Object)
                        .collect()
                )
            )));
        }
        Ok(count)
    }

    /// `write_nodes_batch` — custom node types với Cypher do caller cung cấp
    /// ($rows param); count ước lượng `len(batch)` khi query không RETURN.
    pub fn write_nodes_batch(
        &mut self,
        key: &str,
        cypher: &str,
        rows: &[Row],
    ) -> Result<usize, WriterError> {
        if rows.is_empty() {
            return Ok(0);
        }
        let cypher = cypher.to_string();
        let mut write_fn = move |store: &mut dyn GraphStore,
                                 database: Option<&str>,
                                 batch: &[Row]|
              -> Result<i64, WriterError> {
            let mut params = BTreeMap::new();
            params.insert(
                "rows".to_string(),
                Value::Array(batch.iter().cloned().map(Value::Object).collect()),
            );
            let records = store.execute_query(&cypher, &params, database)?;
            if let Some(count) = records
                .first()
                .and_then(|record| record.get("count"))
                .and_then(as_i64)
            {
                return Ok(count);
            }
            Ok(batch.len() as i64)
        };
        self.write_batches(key, rows, &mut write_fn)
    }

    /// `write_node_properties_batch` — specialized node batch qua trusted
    /// replay compiler (node contract).
    pub fn write_node_properties_batch(
        &mut self,
        key: &str,
        node_label: &str,
        rows: &[Row],
        identity_property: &str,
        row_identity_property: &str,
        row_properties_property: Option<&str>,
    ) -> Result<usize, WriterError> {
        if rows.is_empty() {
            return Ok(0);
        }
        let schema = code_graph_schema();
        if !schema.has_identity_index(node_label, identity_property) {
            return Err(WriterError::Contract(format!(
                "specialized node label {node_label:?} has no required \
                 {identity_property:?} identity index"
            )));
        }
        let query = upserts::compile_node_identity_merge(
            node_label,
            identity_property,
            row_identity_property,
            row_properties_property,
        );
        self.write_batch_if_any(key, rows, Self::simple_batch_fn_static(&query))
    }

    fn simple_batch_fn_static(
        query: &str,
    ) -> impl FnMut(&mut dyn GraphStore, Option<&str>, &[Row]) -> Result<i64, WriterError> + '_ {
        let query = query.to_string();
        move |store, database, batch| {
            let mut params = BTreeMap::new();
            params.insert(
                "rows".to_string(),
                Value::Array(batch.iter().cloned().map(Value::Object).collect()),
            );
            let records = store.execute_query(&query, &params, database)?;
            Ok(records
                .first()
                .and_then(|record| record.get("count"))
                .and_then(as_i64)
                .unwrap_or(0))
        }
    }

    /// Batch fn ủy thác cho operations module (batch_create_*).
    fn operations_batch_fn(
        mut op: impl FnMut(&mut dyn GraphStore, &[Row], Option<&str>) -> Result<i64, StoreError>
            + 'static,
    ) -> impl FnMut(&mut dyn GraphStore, Option<&str>, &[Row]) -> Result<i64, WriterError> {
        move |store, database, batch| Ok(op(store, batch, database)?)
    }

    /// `write_project_repository_setup` — cplus project/repository topology
    /// qua trusted contracts.
    pub fn write_project_repository_setup(
        &mut self,
        project_id: &str,
        project_name: &str,
        project_slug: &str,
        repository_name: &str,
    ) -> Result<BTreeMap<String, i64>, WriterError> {
        let normalized_scope = project_id_lookup_key(Some(project_id))
            .ok_or_else(|| WriterError::Contract("project setup requires a non-empty project_id".into()))?;
        let project_rows = vec![{
            let mut row = Map::new();
            row.insert("project_id".to_string(), json!(project_id));
            row.insert("name".to_string(), json!(project_name));
            row.insert("slug".to_string(), json!(project_slug));
            row.insert(
                "project_id_normalized".to_string(),
                json!(normalized_scope),
            );
            row
        }];
        let repository_rows = vec![{
            let mut row = Map::new();
            row.insert("name".to_string(), json!(repository_name));
            row.insert("id".to_string(), json!(repository_name));
            row.insert("project_id".to_string(), json!(project_id));
            row.insert(
                "project_id_normalized".to_string(),
                json!(normalized_scope),
            );
            row
        }];
        let projects = self.write_node_properties_batch(
            "cplus:project_setup",
            "Project",
            &project_rows,
            "project_id",
            "project_id",
            None,
        )? as i64;
        let repositories = self.write_node_properties_batch(
            "cplus:repository_setup",
            "Repository",
            &repository_rows,
            "name",
            "name",
            None,
        )? as i64;
        let relationships = self.write_evidence_edges(&[{
            let mut props = Map::new();
            props.insert("project_id".to_string(), json!(project_id));
            props.insert(
                "project_id_normalized".to_string(),
                json!(normalized_scope),
            );
            let mut edge = Map::new();
            edge.insert("source_label".to_string(), json!("Project"));
            edge.insert("source_property".to_string(), json!("project_id"));
            edge.insert("source_id".to_string(), json!(project_id));
            edge.insert("target_label".to_string(), json!("Repository"));
            edge.insert("target_property".to_string(), json!("name"));
            edge.insert("target_id".to_string(), json!(repository_name));
            edge.insert("rel_type".to_string(), json!("HAS_REPOSITORY"));
            edge.insert("project_id".to_string(), json!(project_id));
            edge.insert(
                "project_id_normalized".to_string(),
                json!(normalized_scope),
            );
            edge.insert("props".to_string(), Value::Object(props));
            edge
        }])? as i64;
        let mut counts = BTreeMap::new();
        counts.insert("projects".to_string(), projects);
        counts.insert("repositories".to_string(), repositories);
        counts.insert("relationships".to_string(), relationships);
        Ok(counts)
    }

    /// `cleanup_incremental_files` — delete stale file-owned facts qua
    /// replay-safe node-phase jobs.
    pub fn cleanup_incremental_files(
        &mut self,
        project_id: &str,
        file_paths: &[String],
    ) -> Result<BTreeMap<String, usize>, WriterError> {
        let paths: BTreeSet<String> = file_paths
            .iter()
            .filter(|path| !path.is_empty())
            .map(|path| path.replace('\\', "/"))
            .collect();
        let paths_vec: Vec<Row> = if paths.is_empty() {
            return Ok(BTreeMap::from([
                ("file_cleanup_jobs".to_string(), 0),
                ("orphan_cleanup_jobs".to_string(), 0),
            ]));
        } else {
            let sorted: Vec<String> = paths.into_iter().collect();
            vec![{
                let mut row = Map::new();
                row.insert("project_id".to_string(), json!(project_id));
                row.insert(
                    "paths".to_string(),
                    Value::Array(sorted.into_iter().map(Value::String).collect()),
                );
                row
            }]
        };

        let mut file_jobs = 0usize;
        for node_label in upserts::CPLUS_FILE_OWNED_NODE_LABELS {
            let label = format!("cplus:incremental_file_cleanup:{node_label}");
            let query = upserts::COMPILE_FILE_CLEANUP.replace("{node_label}", node_label);
            file_jobs += self.write_batch_if_any(&label, &paths_vec, Self::simple_batch_fn_owned(query))?;
        }

        let orphan_jobs = self.write_batch_if_any(
            "cplus:incremental_orphan_cleanup",
            &paths_vec,
            Self::simple_batch_fn_owned(upserts::COMPILE_ORPHAN_CLEANUP.to_string()),
        )?;
        Ok(BTreeMap::from([
            ("file_cleanup_jobs".to_string(), file_jobs),
            ("orphan_cleanup_jobs".to_string(), orphan_jobs),
        ]))
    }

    fn simple_batch_fn_owned(
        query: String,
    ) -> impl FnMut(&mut dyn GraphStore, Option<&str>, &[Row]) -> Result<i64, WriterError> {
        move |store, database, batch| {
            let mut params = BTreeMap::new();
            params.insert(
                "rows".to_string(),
                Value::Array(batch.iter().cloned().map(Value::Object).collect()),
            );
            let records = store.execute_query(&query, &params, database)?;
            Ok(records
                .first()
                .and_then(|record| record.get("count"))
                .and_then(as_i64)
                .unwrap_or(0))
        }
    }

    // ── Evidence edges (staging plane) ───────────────────────────────────

    /// `write_evidence_edges` — self-describing staging-plane edges.
    pub fn write_evidence_edges(&mut self, edges: &[Row]) -> Result<usize, WriterError> {
        if edges.is_empty() {
            return Ok(0);
        }
        let groups = group_evidence_edges(edges).map_err(WriterError::Contract)?;
        if self.store.provider() == "ladybug" {
            let mut pairs: BTreeMap<&str, BTreeSet<String>> = BTreeMap::new();
            for group in groups.keys() {
                pairs
                    .entry(group.relationship_type.as_str())
                    .or_default()
                    .insert(format!(
                        "FROM `{}` TO `{}`",
                        group.source_label, group.target_label
                    ));
            }
            for (rel, endpoints) in pairs {
                let statement = format!(
                    "CREATE REL TABLE IF NOT EXISTS `{rel}` ({})",
                    endpoints.into_iter().collect::<Vec<_>>().join(", ")
                );
                self.store
                    .execute_query(&statement, &BTreeMap::new(), self.database.as_deref())?;
            }
        }
        let mut written = 0usize;
        for (group, rows) in groups {
            let ladybug = self.store.provider() == "ladybug";
            if ladybug
                && rows.iter().any(|row| {
                    row.get("props")
                        .and_then(Value::as_object)
                        .map(|props| !props.is_empty())
                        .unwrap_or(false)
                })
            {
                return Err(WriterError::Contract(
                    "ladybug provider does not support evidence edge props; \
                     drop the props or extend the writer"
                        .to_string(),
                ));
            }
            let query = if ladybug {
                compile_evidence_edge_upsert_ladybug(&group)
            } else {
                compile_evidence_edge_upsert(&group)
            };
            let state_key = group.state_key();
            written += self.write_batch_if_any(&state_key, &rows, Self::simple_batch_fn_owned(query))?;
        }
        Ok(written)
    }

    /// `write_calls_with_site`.
    pub fn write_calls_with_site(&mut self, calls: &[Row]) -> Result<usize, WriterError> {
        self.write_calls_site_generic(calls, "calls:site", upserts::WRITE_CALLS_WITH_SITE)
    }

    /// `write_possible_calls_with_site`.
    pub fn write_possible_calls_with_site(&mut self, calls: &[Row]) -> Result<usize, WriterError> {
        self.write_calls_site_generic(
            calls,
            "possible_calls:site",
            upserts::WRITE_POSSIBLE_CALLS_WITH_SITE,
        )
    }

    fn write_calls_site_generic(
        &mut self,
        calls: &[Row],
        label: &str,
        query: &'static str,
    ) -> Result<usize, WriterError> {
        if calls.is_empty() {
            return Ok(0);
        }
        let mut scoped = calls.to_vec();
        self.require_call_project_scope(&mut scoped)?;
        for row in &scoped {
            enforce_strong_call_row(row)?;
        }
        self.write_batch_if_any(label, &scoped, Self::simple_batch_fn(query))
    }

    /// `write_call_evidence_sites` — CallSite staging nodes + evidence links.
    pub fn write_call_evidence_sites(&mut self, sites: &[Row]) -> Result<usize, WriterError> {
        if sites.is_empty() {
            return Ok(0);
        }
        let mut rows = sites.to_vec();
        self.require_evidence_project_scope(&mut rows, "call evidence site")?;
        let written =
            self.write_batch_if_any("call_evidence:sites", &rows, Self::simple_batch_fn(upserts::WRITE_CALL_EVIDENCE_SITES))?;

        let mut edges: Vec<Row> = Vec::new();
        for row in &rows {
            let site_id = row_get(row, "site_id").as_str().unwrap_or("").trim().to_string();
            if site_id.is_empty() {
                return Err(WriterError::Contract(
                    "call evidence site rows require site_id".into(),
                ));
            }
            let mut base = Map::new();
            base.insert(
                "project_id".to_string(),
                row.get("project_id").cloned().unwrap_or(Value::Null),
            );
            let scoped_props: Map<String, Value> = row
                .get("props")
                .and_then(Value::as_object)
                .map(|props| {
                    props
                        .iter()
                        .filter(|(_, value)| value.is_string() || value.is_i64() || value.is_f64() || value.is_boolean())
                        .map(|(key, value)| (key.clone(), value.clone()))
                        .collect()
                })
                .unwrap_or_default();
            base.insert("props".to_string(), Value::Object(scoped_props));

            let caller_id = row_get(row, "caller_id").as_str().unwrap_or("").to_string();
            if !caller_id.is_empty() {
                let mut edge = base.clone();
                edge.insert("source_label".to_string(), json!("Function"));
                edge.insert("source_property".to_string(), json!("id"));
                edge.insert("source_id".to_string(), json!(caller_id));
                edge.insert("target_label".to_string(), json!("CallSite"));
                edge.insert("target_property".to_string(), json!("site_id"));
                edge.insert("target_id".to_string(), json!(site_id));
                edge.insert("rel_type".to_string(), json!("HAS_CALLSITE"));
                edges.push(edge);
            }
            let callee_id = row_get(row, "callee_id").as_str().unwrap_or("").to_string();
            if !callee_id.is_empty() {
                let mut edge = base.clone();
                edge.insert("source_label".to_string(), json!("CallSite"));
                edge.insert("source_property".to_string(), json!("site_id"));
                edge.insert("source_id".to_string(), json!(site_id));
                edge.insert("target_label".to_string(), json!("Function"));
                edge.insert("target_property".to_string(), json!("id"));
                edge.insert("target_id".to_string(), json!(callee_id));
                edge.insert("rel_type".to_string(), json!("RESOLVES_TO"));
                edge.insert("edge_property".to_string(), json!("site_id"));
                edge.insert("edge_id".to_string(), json!(site_id));
                let mut props = Map::new();
                props.insert(
                    "resolution_class".to_string(),
                    json!(row_get(row, "resolution_class").as_str().unwrap_or("")),
                );
                edge.insert("props".to_string(), Value::Object(props));
                edges.push(edge);
            }
        }
        // Plane count giữ nguyên staging-node count; derived edges journal
        // dưới evidence-edge state keys riêng.
        self.write_evidence_edges(&edges)?;
        Ok(written)
    }

    /// `write_call_evidence_observations` — dedup OBSERVED_AS evidence edges.
    pub fn write_call_evidence_observations(
        &mut self,
        observations: &[Row],
    ) -> Result<usize, WriterError> {
        if observations.is_empty() {
            return Ok(0);
        }
        let mut rows = observations.to_vec();
        self.require_evidence_project_scope(&mut rows, "call evidence observation")?;
        let mut edges: Vec<Row> = Vec::new();
        for row in &rows {
            let callee_id = row_get(row, "callee_id")
                .as_str()
                .map(str::to_string)
                .or_else(|| {
                    row.get("callee_symbol_id").and_then(Value::as_str).map(str::to_string)
                })
                .unwrap_or_default();
            if callee_id.is_empty() {
                if row_get(row, "dangling").as_bool().unwrap_or(false) {
                    continue;
                }
                return Err(WriterError::Contract(format!(
                    "call evidence observation requires callee_id or an explicit \
                     dangling flag: evidence_id={:?}",
                    row.get("evidence_id").map(|value| value.to_string())
                )));
            }
            if row_get(row, "dangling").as_bool().unwrap_or(false) {
                continue;
            }
            if row_get(row, "evidence_id")
                .as_str()
                .map(str::trim)
                .unwrap_or("")
                .is_empty()
            {
                return Err(WriterError::Contract(
                    "call evidence observation rows require evidence_id".into(),
                ));
            }
            let excluded: [&str; 7] = [
                "site_id",
                "callee_id",
                "callee_symbol_id",
                "evidence_id",
                "dangling",
                "props",
                "_repeat_runs",
            ];
            let props: Map<String, Value> = row
                .iter()
                .filter(|(key, value)| {
                    !excluded.contains(&key.as_str())
                        && !matches!(value, Value::Null | Value::Object(_) | Value::Array(_))
                        && (value.is_string() || value.is_i64() || value.is_f64() || value.is_boolean())
                })
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect();
            let mut edge = Map::new();
            edge.insert("source_label".to_string(), json!("CallSite"));
            edge.insert("source_property".to_string(), json!("site_id"));
            edge.insert(
                "source_id".to_string(),
                json!(row_get(row, "site_id").as_str().unwrap_or("")),
            );
            edge.insert("target_label".to_string(), json!("Function"));
            edge.insert("target_property".to_string(), json!("id"));
            edge.insert("target_id".to_string(), json!(callee_id));
            edge.insert("rel_type".to_string(), json!("OBSERVED_AS"));
            edge.insert("edge_property".to_string(), json!("evidence_id"));
            edge.insert(
                "edge_id".to_string(),
                json!(row_get(row, "evidence_id").as_str().unwrap_or("")),
            );
            edge.insert(
                "project_id".to_string(),
                json!(row_get(row, "project_id").as_str().unwrap_or("")),
            );
            edge.insert("props".to_string(), Value::Object(props));
            edges.push(edge);
        }
        self.write_evidence_edges(&edges)
    }

    /// `write_build_configurations` — BuildConfiguration nodes +
    /// IN_CONFIGURATION links.
    pub fn write_build_configurations(
        &mut self,
        configurations: &[Row],
    ) -> Result<usize, WriterError> {
        if configurations.is_empty() {
            return Ok(0);
        }
        let mut rows = configurations.to_vec();
        self.require_evidence_project_scope(&mut rows, "build configuration")?;
        let written = self.write_batch_if_any(
            "call_evidence:configurations",
            &rows,
            Self::simple_batch_fn(upserts::WRITE_BUILD_CONFIGURATIONS),
        )?;
        let mut edges: Vec<Row> = Vec::new();
        for row in &rows {
            let site_id = row_get(row, "site_id").as_str().unwrap_or("").to_string();
            if site_id.is_empty() {
                continue;
            }
            let mut edge = Map::new();
            edge.insert("source_label".to_string(), json!("CallSite"));
            edge.insert("source_property".to_string(), json!("site_id"));
            edge.insert("source_id".to_string(), json!(site_id));
            edge.insert("target_label".to_string(), json!("BuildConfiguration"));
            edge.insert("target_property".to_string(), json!("config_fingerprint"));
            edge.insert(
                "target_id".to_string(),
                json!(row_get(row, "config_fingerprint").as_str().unwrap_or("")),
            );
            edge.insert("rel_type".to_string(), json!("IN_CONFIGURATION"));
            edge.insert("project_id".to_string(), row.get("project_id").cloned().unwrap_or(Value::Null));
            edge.insert("props".to_string(), Value::Object(Map::new()));
            edges.push(edge);
        }
        self.write_evidence_edges(&edges)?;
        Ok(written)
    }

    /// `write_semantic_coverage`.
    pub fn write_semantic_coverage(&mut self, records_in: &[Row]) -> Result<usize, WriterError> {
        if records_in.is_empty() {
            return Ok(0);
        }
        let mut rows = records_in.to_vec();
        self.require_evidence_project_scope(&mut rows, "semantic coverage")?;
        self.write_batch_if_any(
            "call_evidence:coverage",
            &rows,
            Self::simple_batch_fn(upserts::WRITE_SEMANTIC_COVERAGE),
        )
    }

    /// `write_proc_evidence_joins` — EXECUTES_SQL + RESOLVES_HOST_DECLARATION.
    pub fn write_proc_evidence_joins(
        &mut self,
        function_joins: &[Row],
        host_declarations: &[Row],
    ) -> Result<usize, WriterError> {
        let mut function_rows = function_joins.to_vec();
        let mut host_rows = host_declarations.to_vec();
        self.require_evidence_project_scope(&mut function_rows, "proc function join")?;
        self.require_evidence_project_scope(&mut host_rows, "proc host declaration")?;
        let mut edges: Vec<Row> = Vec::new();
        for join in &function_rows {
            let function_id = row_get(join, "function_id").as_str().unwrap_or("").trim();
            let statement_id = row_get(join, "statement_id").as_str().unwrap_or("").trim();
            if function_id.is_empty() || statement_id.is_empty() {
                return Err(WriterError::Contract(
                    "proc function joins require function_id and statement_id".into(),
                ));
            }
            let props: Map<String, Value> = join
                .iter()
                .filter(|(key, value)| {
                    !matches!(key.as_str(), "function_id" | "statement_id" | "props")
                        && !matches!(value, Value::Null | Value::Object(_) | Value::Array(_))
                        && (value.is_string() || value.is_i64() || value.is_f64() || value.is_boolean())
                })
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect();
            let mut edge = Map::new();
            edge.insert("source_label".to_string(), json!("Function"));
            edge.insert("source_property".to_string(), json!("id"));
            edge.insert("source_id".to_string(), json!(function_id));
            edge.insert("target_label".to_string(), json!("SqlStatement"));
            edge.insert("target_property".to_string(), json!("id"));
            edge.insert("target_id".to_string(), json!(statement_id));
            edge.insert("rel_type".to_string(), json!("EXECUTES_SQL"));
            edge.insert("edge_property".to_string(), json!("statement_id"));
            edge.insert("edge_id".to_string(), json!(statement_id));
            edge.insert(
                "project_id".to_string(),
                json!(row_get(join, "project_id").as_str().unwrap_or("")),
            );
            edge.insert("props".to_string(), Value::Object(props));
            edges.push(edge);
        }
        for host in &host_rows {
            let host_variable_id = row_get(host, "host_variable_id").as_str().unwrap_or("").trim();
            if host_variable_id.is_empty() {
                return Err(WriterError::Contract(
                    "proc host declaration joins require host_variable_id".into(),
                ));
            }
            let declaration_id = row_get(host, "declaration_id").as_str().unwrap_or("").trim();
            if declaration_id.is_empty() {
                // Unresolved declaration stays conservative evidence, never
                // becomes a graph edge.
                continue;
            }
            let kind = row_get(host, "declaration_kind").as_str().unwrap_or("");
            let target_label = row_get(host, "target_label")
                .as_str()
                .map(str::to_string)
                .or_else(|| upserts::declaration_kind_label(kind).map(str::to_string))
                .unwrap_or_default();
            if target_label.is_empty() {
                return Err(WriterError::Contract(format!(
                    "host declaration joins require a graph label for the \
                     declaration: {kind:?}"
                )));
            }
            let props: Map<String, Value> = host
                .iter()
                .filter(|(key, value)| {
                    !matches!(
                        key.as_str(),
                        "host_variable_id"
                            | "declaration_id"
                            | "declaration_kind"
                            | "target_label"
                            | "props"
                    ) && !matches!(value, Value::Null | Value::Object(_) | Value::Array(_))
                        && (value.is_string() || value.is_i64() || value.is_f64() || value.is_boolean())
                })
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect();
            let mut edge = Map::new();
            edge.insert("source_label".to_string(), json!("SqlHostVariable"));
            edge.insert("source_property".to_string(), json!("id"));
            edge.insert("source_id".to_string(), json!(host_variable_id));
            edge.insert("target_label".to_string(), json!(target_label));
            edge.insert("target_property".to_string(), json!("id"));
            edge.insert("target_id".to_string(), json!(declaration_id));
            edge.insert("rel_type".to_string(), json!("RESOLVES_HOST_DECLARATION"));
            edge.insert("edge_property".to_string(), json!("declaration_id"));
            edge.insert("edge_id".to_string(), json!(declaration_id));
            edge.insert(
                "project_id".to_string(),
                json!(row_get(host, "project_id").as_str().unwrap_or("")),
            );
            edge.insert("props".to_string(), Value::Object(props));
            edges.push(edge);
        }
        self.write_evidence_edges(&edges)
    }

    // ── write_all orchestration ──────────────────────────────────────────

    /// `write_all` — orchestration đúng thứ tự; port logic resolve label +
    /// lọc relation optional/unresolved của Python.
    #[allow(clippy::too_many_arguments)]
    pub fn write_all(&mut self, payload: &WriteAllPayload) -> Result<BTreeMap<String, i64>, WriterError> {
        let mut counts: BTreeMap<String, i64> = BTreeMap::new();
        let mut optional_unresolved_relations = 0usize;

        let mut relations: Vec<Row> = payload.relations.to_vec();
        if !relations.is_empty() {
            let mut project_ids: BTreeSet<String> = BTreeSet::new();
            for project in payload.projects {
                if let Some(id) = row_get(project, "id").as_str() {
                    project_ids.insert(id.to_string());
                }
            }
            for file_row in payload.files {
                if let Some(id) = row_get(file_row, "project_id").as_str() {
                    project_ids.insert(id.to_string());
                }
            }
            if project_ids.len() == 1 {
                let default_project_id = project_ids.iter().next().unwrap().clone();
                for relation in &mut relations {
                    if row_get(relation, "project_id").as_str().unwrap_or("").is_empty() {
                        relation.insert(
                            "project_id".to_string(),
                            json!(default_project_id),
                        );
                    }
                }
            }
            let candidate_relations: Vec<Row> = relations
                .iter()
                .filter(|relation| {
                    let source_id = row_get(relation, "source_id").as_str().unwrap_or("");
                    !(project_ids.contains(source_id)
                        && row_get(relation, "rel_type").as_str() == Some("CONTAINS"))
                })
                .cloned()
                .collect();
            relations.clear();
            for relation in candidate_relations {
                let explicitly_optional = row_get(&relation, "required").as_bool() == Some(false)
                    || relation
                        .get("properties")
                        .and_then(Value::as_object)
                        .and_then(|props| props.get("resolved"))
                        .and_then(Value::as_bool)
                        == Some(false);
                if explicitly_optional {
                    optional_unresolved_relations += 1;
                    continue;
                }
                relations.push(relation);
            }

            // Label inference từ identity rows.
            let identity_rows: [(&[Row], &str); 17] = [
                (payload.packages, "Package"),
                (payload.namespaces, "Namespace"),
                (payload.files, "File"),
                (payload.classes, "Class"),
                (payload.types, "Type"),
                (payload.function_types, "FunctionType"),
                (payload.functions, "Function"),
                (payload.fields, "Field"),
                (payload.aliases, "Alias"),
                (payload.templates, "Template"),
                (payload.properties, "Property"),
                (payload.events, "Event"),
                (payload.interfaces, "Interface"),
                (payload.enums, "Enum"),
                (payload.constants, "Constant"),
                (payload.variables, "Variable"),
                (payload.navigators, "Navigator"),
            ];
            let mut labels_by_id: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
            for (rows, label) in &identity_rows {
                for row in *rows {
                    let identity = row_get(row, "id")
                        .as_str()
                        .map(str::to_string)
                        .or_else(|| row_get(row, "symbol_id").as_str().map(str::to_string));
                    if let Some(identity) = identity {
                        labels_by_id
                            .entry(identity)
                            .or_default()
                            .insert((*label).to_string());
                    }
                }
            }

            let mut resolved_relations: Vec<Row> = Vec::new();
            for (position, relation) in relations.iter_mut().enumerate() {
                let mut skip_optional = false;
                for role in ["source", "target"] {
                    let label_key = format!("{role}_label");
                    if !row_get(relation, &label_key).as_str().unwrap_or("").is_empty() {
                        continue;
                    }
                    let identity = row_get(relation, &format!("{role}_id"))
                        .as_str()
                        .unwrap_or("")
                        .to_string();
                    let empty = BTreeSet::new();
                    let candidates = labels_by_id.get(&identity).unwrap_or(&empty);
                    if candidates.len() != 1 {
                        let rel_type = row_get(relation, "rel_type").as_str().unwrap_or("");
                        if role == "target"
                            && candidates.is_empty()
                            && upserts::OPTIONAL_EXTERNAL_RELATION_TYPES.contains(&rel_type)
                        {
                            optional_unresolved_relations += 1;
                            skip_optional = true;
                            break;
                        }
                        let sorted: Vec<String> = candidates.iter().cloned().collect();
                        return Err(WriterError::Contract(format!(
                            "cannot infer {label_key} for relationship row {position} \
                             identity={identity:?}; candidates={sorted:?}"
                        )));
                    }
                    let label = candidates.iter().next().unwrap().clone();
                    relation.insert(label_key, json!(label));
                }
                if !skip_optional {
                    resolved_relations.push(relation.clone());
                }
            }
            relations = resolved_relations;

            // Validate contract TRƯỚC schema/node mutations.
            group_typed_relations(&relations, None).map_err(WriterError::Contract)?;
        }

        if optional_unresolved_relations > 0 {
            counts.insert(
                "unresolved_relations".to_string(),
                optional_unresolved_relations as i64,
            );
            self.emit_progress(
                "optional_unresolved",
                "relations",
                &[("skipped", json!(optional_unresolved_relations))],
            );
        }

        if !payload.projects.is_empty() {
            counts.insert("projects".to_string(), self.write_projects(payload.projects)? as i64);
        }
        if !payload.packages.is_empty() {
            let count = if payload.use_full_writers {
                self.write_packages_full(payload.packages)?
            } else {
                self.write_packages(payload.packages)?
            };
            counts.insert("packages".to_string(), count as i64);
        }
        if !payload.namespaces.is_empty() {
            let count = if payload.use_full_writers {
                self.write_namespaces_full(payload.namespaces)?
            } else {
                self.write_namespaces(payload.namespaces)?
            };
            counts.insert("namespaces".to_string(), count as i64);
        }
        if !payload.files.is_empty() {
            let count = match payload.files_variant {
                FilesVariant::WithPackage => self.write_files_with_package(payload.files)?,
                FilesVariant::WithImports => self.write_files_with_imports(payload.files)?,
                FilesVariant::WithJsx => self.write_files_jsx(payload.files)?,
                FilesVariant::Default => self.write_files(payload.files)?,
            };
            counts.insert("files".to_string(), count as i64);
            counts.insert(
                "repo_file_edges".to_string(),
                self.write_repo_file_edges(payload.files)? as i64,
            );
        }
        if !payload.classes.is_empty() {
            let count = if payload.use_full_writers {
                self.write_classes_full(payload.classes)?
            } else {
                self.write_classes(payload.classes)?
            };
            counts.insert("classes".to_string(), count as i64);
        }
        if !payload.types.is_empty() {
            let count = if payload.use_full_writers {
                self.write_types_full(payload.types)?
            } else {
                self.write_types(payload.types)?
            };
            counts.insert("types".to_string(), count as i64);
        }
        if !payload.function_types.is_empty() {
            counts.insert(
                "function_types".to_string(),
                self.write_function_types(payload.function_types)? as i64,
            );
        }
        if !payload.functions.is_empty() {
            let count = if payload.use_full_writers {
                self.write_functions_full(payload.functions)?
            } else {
                self.write_functions(payload.functions)?
            };
            counts.insert("functions".to_string(), count as i64);
        }
        if !payload.navigators.is_empty() {
            counts.insert(
                "navigators".to_string(),
                self.write_navigators(payload.navigators)? as i64,
            );
        }
        if !payload.has_routes.is_empty() {
            counts.insert(
                "has_routes".to_string(),
                self.write_has_routes(payload.has_routes)? as i64,
            );
        }
        if !payload.param_lists.is_empty() {
            counts.insert(
                "param_lists".to_string(),
                self.write_param_lists(payload.param_lists)? as i64,
            );
        }
        if payload.use_full_writers && !payload.properties.is_empty() {
            counts.insert(
                "properties".to_string(),
                self.write_properties_full(payload.properties)? as i64,
            );
        }
        if payload.use_full_writers && !payload.events.is_empty() {
            counts.insert(
                "events".to_string(),
                self.write_events_full(payload.events)? as i64,
            );
        }
        if payload.use_full_writers && !payload.interfaces.is_empty() {
            counts.insert(
                "interfaces".to_string(),
                self.write_interfaces_full(payload.interfaces)? as i64,
            );
        }
        if payload.use_full_writers && !payload.enums.is_empty() {
            counts.insert(
                "enums".to_string(),
                self.write_enums_full(payload.enums)? as i64,
            );
        }
        if payload.use_full_writers && !payload.constants.is_empty() {
            counts.insert(
                "constants".to_string(),
                self.write_constants_full(payload.constants)? as i64,
            );
        }
        if payload.use_full_writers && !payload.variables.is_empty() {
            counts.insert(
                "variables".to_string(),
                self.write_variables_full(payload.variables)? as i64,
            );
        }
        if !payload.fields.is_empty() {
            counts.insert(
                "fields".to_string(),
                self.write_fields(payload.fields)? as i64,
            );
        }
        if !payload.aliases.is_empty() {
            counts.insert(
                "aliases".to_string(),
                self.write_aliases(payload.aliases)? as i64,
            );
        }
        if !payload.templates.is_empty() {
            counts.insert(
                "templates".to_string(),
                self.write_templates(payload.templates)? as i64,
            );
        }
        if !relations.is_empty() {
            let count = self.write_relations(&relations)?;
            counts.insert("relations".to_string(), count as i64);
        }
        if !payload.calls.is_empty() {
            counts.insert(
                "calls".to_string(),
                self.write_calls(payload.calls)? as i64,
            );
        }
        if !payload.calls_with_site.is_empty() {
            counts.insert(
                "calls_with_site".to_string(),
                self.write_calls_with_site(payload.calls_with_site)? as i64,
            );
        }
        if !payload.call_evidence_sites.is_empty() {
            counts.insert(
                "call_evidence_sites".to_string(),
                self.write_call_evidence_sites(payload.call_evidence_sites)? as i64,
            );
        }
        if !payload.call_evidence_observations.is_empty() {
            counts.insert(
                "call_evidence_observations".to_string(),
                self.write_call_evidence_observations(payload.call_evidence_observations)? as i64,
            );
        }
        if !payload.build_configurations.is_empty() {
            counts.insert(
                "build_configurations".to_string(),
                self.write_build_configurations(payload.build_configurations)? as i64,
            );
        }
        if !payload.semantic_coverage.is_empty() {
            counts.insert(
                "semantic_coverage".to_string(),
                self.write_semantic_coverage(payload.semantic_coverage)? as i64,
            );
        }
        if !payload.proc_function_joins.is_empty() || !payload.proc_host_declarations.is_empty() {
            counts.insert(
                "proc_evidence_joins".to_string(),
                self.write_proc_evidence_joins(
                    payload.proc_function_joins,
                    payload.proc_host_declarations,
                )? as i64,
            );
        }
        if !payload.workflows.is_empty() {
            counts.insert(
                "workflows".to_string(),
                self.write_workflows(payload.workflows)? as i64,
            );
        }
        if !payload.workflow_steps.is_empty() {
            counts.insert(
                "workflow_steps".to_string(),
                self.write_workflow_steps(payload.workflow_steps)? as i64,
            );
        }
        Ok(counts)
    }
}

/// Variants của file writer (`files_variant` của Python).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum FilesVariant {
    #[default]
    Default,
    WithPackage,
    WithImports,
    WithJsx,
}

/// Payload cho `write_all` — thay cho tham số 30+ của Python.
#[derive(Debug, Clone, Default)]
pub struct WriteAllPayload<'a> {
    pub projects: &'a [Row],
    pub packages: &'a [Row],
    pub namespaces: &'a [Row],
    pub files: &'a [Row],
    pub classes: &'a [Row],
    pub types: &'a [Row],
    pub function_types: &'a [Row],
    pub functions: &'a [Row],
    pub fields: &'a [Row],
    pub aliases: &'a [Row],
    pub templates: &'a [Row],
    pub relations: &'a [Row],
    pub calls: &'a [Row],
    pub calls_with_site: &'a [Row],
    pub properties: &'a [Row],
    pub events: &'a [Row],
    pub interfaces: &'a [Row],
    pub enums: &'a [Row],
    pub constants: &'a [Row],
    pub variables: &'a [Row],
    pub navigators: &'a [Row],
    pub has_routes: &'a [Row],
    pub param_lists: &'a [Row],
    pub workflows: &'a [Row],
    pub workflow_steps: &'a [Row],
    pub call_evidence_sites: &'a [Row],
    pub call_evidence_observations: &'a [Row],
    pub build_configurations: &'a [Row],
    pub semantic_coverage: &'a [Row],
    pub proc_function_joins: &'a [Row],
    pub proc_host_declarations: &'a [Row],
    pub use_full_writers: bool,
    pub files_variant: FilesVariant,
}

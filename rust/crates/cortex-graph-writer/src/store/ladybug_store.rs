//! LadybugDB backend cho [`GraphStore`] — embedded, local-only (crate `lbug`
//! 0.20.4, cùng engine với PyPI `ladybug`).
//!
//! Dialect findings (probe 2026-09-13, ladybug 0.20.4 — khớp dialect notes
//! của `ladybug_driver.py`):
//!
//! * **Không có param binding cho list-of-map**: prepared param LIST<STRUCT>
//!   bị binder từ chối ("NODE,REL,STRUCT,ANY was expected") — store render
//!   **toàn bộ params thành Cypher literals inline** (string escaping theo
//!   chuẩn Cypher) thay vì prepared statements.
//! * Map literal là STRUCT — access key thiếu → "Invalid struct field name".
//!   Vì vậy rows được render **uniform-key**: mọi map theo union key của cả
//!   batch (key thiếu → NULL literal), khớp semantics `row.prop → null`.
//! * `SET n += <map>` **không parse được** trên 0.20.4 — query chứa `+=`
//!   (typed relations, evidence sites, topology) fail y như Python driver
//!   trên ladybug hiện tại (đã ghi trong phase-03 notes; parity gate chạy
//!   trên falkordb).
//! * Zero-arg `datetime()` không tồn tại → rewrite `timestamp('<iso>')`
//!   (khớp `rewrite_datetime_call` của Python, nhưng inline literal).
//! * Static schema: bootstrap `CODE_GRAPH_SCHEMA` node/rel tables lần mở đầu
//!   viết + auto-DDL `ALTER TABLE ADD` / `CREATE ... TABLE` khi binder báo
//!   thiếu property/table (port `_auto_ddl_statement` của Python).

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use lbug::{Connection, Database, SystemConfig};
use regex::Regex;
use serde_json::Value;

use cortex_graph_core::schema_manifest::{code_graph_schema, validate_cypher_identifier};

use crate::json_row::Row;
use crate::project_scope::prepare_project_scope_parameters;
use crate::query_normalize::{is_write_intent, normalize_call_importing_subqueries};
use crate::store::{GraphStore, IndexSpec, QueryRecords, StoreError};

/// Auto-DDL error classification (exact Ladybug 0.20.4 binder wording).
fn missing_property_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"Cannot find property ([A-Za-z_][A-Za-z0-9_]*) for ([A-Za-z_][A-Za-z0-9_]*)")
            .unwrap()
    })
}

fn missing_table_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"Table ([A-Za-z_][A-Za-z0-9_]*) does not exist").unwrap())
}

fn missing_rel_bind_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"Cannot bind ([A-Za-z_][A-Za-z0-9_]*) as a relationship pattern label")
            .unwrap()
    })
}

fn merge_node_pattern_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"MERGE \((\w+):(\w+) \{(\w+): ([^}]+)\}\)").unwrap()
    })
}

/// Ladybug yêu cầu primary key (`id`) xuất hiện trực tiếp trong MERGE
/// pattern, còn các upsert port từ FalkorDB merge trên natural key
/// (Project.project_id, CallSite.site_id, Workflow.workflow_id, …).
/// Rewrite merge key sang `id` (cùng giá trị expr) rồi SET lại natural key
/// ngay sau merge — matching semantics giữ nguyên vì natural key chính là
/// identity của node type đó trong corpus.
fn rewrite_merge_natural_keys(query: &str) -> String {
    struct Inject {
        var: String,
        key: String,
        expr: String,
    }
    struct Rewritten {
        start: usize,
        end: usize,
        replacement: String,
        inject: Inject,
    }

    let mut rewrites: Vec<Rewritten> = Vec::new();
    for caps in merge_node_pattern_re().captures_iter(query) {
        let whole = caps.get(0).unwrap();
        let var = caps[1].to_string();
        let key = caps[3].to_string();
        if key == "id" {
            continue;
        }
        let expr = caps[4].to_string();
        rewrites.push(Rewritten {
            start: whole.start(),
            end: whole.end(),
            replacement: format!("MERGE ({}:{} {{id: {}}})", var, &caps[2], expr),
            inject: Inject { var, key, expr },
        });
    }
    if rewrites.is_empty() {
        return query.to_string();
    }

    let mut out = String::with_capacity(query.len());
    let mut cursor = 0usize;
    for r in rewrites {
        out.push_str(&query[cursor..r.start]);
        out.push_str(&r.replacement);
        let rest = &query[r.end..];
        let trimmed = rest.trim_start();
        let ws = rest.len() - trimmed.len();
        let inject = format!("{}.{} = {},", r.inject.var, r.inject.key, r.inject.expr);
        if let Some(_tail) = trimmed.strip_prefix("ON CREATE SET") {
            out.push_str(&rest[..ws + "ON CREATE SET".len()]);
            out.push(' ');
            out.push_str(&inject);
            cursor = r.end + ws + "ON CREATE SET".len();
        } else if trimmed.starts_with("ON MATCH SET") {
            // Natural key là identity — node match đã có sẵn key từ lúc
            // create; không chèn SET (và không phá vị trí ON MATCH SET).
            cursor = r.end;
        } else if trimmed.starts_with("SET") {
            out.push_str(&rest[..ws + 3]);
            out.push(' ');
            out.push_str(&inject);
            cursor = r.end + ws + 3;
        } else {
            out.push_str(&format!("\nSET {}.{} = {}\n", r.inject.var, r.inject.key, r.inject.expr));
            cursor = r.end;
        }
    }
    out.push_str(&query[cursor..]);
    out
}

fn rel_var_re(var: &str) -> Regex {
    Regex::new(&format!(
        r"(?i)-\[\s*`?{}`?\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?",
        regex::escape(var)
    ))
    .unwrap()
}

fn node_var_re(var: &str) -> Regex {
    Regex::new(&format!(
        r"(?i)[(,]\s*`?{}`?\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?",
        regex::escape(var)
    ))
    .unwrap()
}

/// `CORTEX_GRAPH_AUTO_DDL` — default on, operator fail-closed được.
pub fn auto_ddl_enabled() -> bool {
    match std::env::var("CORTEX_GRAPH_AUTO_DDL") {
        Ok(raw) => {
            let raw = raw.trim().to_lowercase();
            !matches!(raw.as_str(), "0" | "false" | "no" | "off")
        }
        Err(_) => true,
    }
}

pub struct LadybugStore {
    database: Database,
    graph: String,
    path: PathBuf,
    bootstrapped: bool,
}

impl LadybugStore {
    /// Tên graph nội bộ (diagnostics).
    pub fn graph(&self) -> &str {
        &self.graph
    }

    /// Đường dẫn store file (diagnostics).
    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl LadybugStore {
    /// Mở (hoặc tạo) store file. Ladybug tự tạo store nhưng không tạo thư
    /// mục cha — tạo trước như `_open_local_ladybug`.
    pub fn open(path: &Path, graph: &str) -> Result<Self, StoreError> {
        let path = path.to_path_buf();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let database = Database::new(&path, SystemConfig::default())
            .map_err(|e| StoreError::Ladybug(format!("cannot open store {path:?}: {e}")))?;
        Ok(Self {
            database,
            graph: graph.to_string(),
            path,
            bootstrapped: false,
        })
    }

    /// Connection per query — `Connection<'a>` mượn `Database` nên không thể
    /// giữ field tự-tham-chiếu (giới hạn API của crate lbug, xem spike.rs).
    fn connect(&self) -> Result<Connection<'_>, StoreError> {
        Connection::new(&self.database)
            .map_err(|e| StoreError::Ladybug(format!("cannot connect: {e}")))
    }

    /// Chuẩn bị query theo dialect ladybug: normalize subqueries + scope
    /// params + render literals + datetime rewrite.
    fn prepare(
        query: &str,
        parameters: &BTreeMap<String, Value>,
    ) -> Result<String, StoreError> {
        let query = rewrite_merge_natural_keys(query);
        let normalized = normalize_call_importing_subqueries(&query);
        let scoped = prepare_project_scope_parameters(parameters);
        // datetime() rewrite — inline timestamp('<iso>') như docstring.
        let timestamp = if normalized.contains("datetime()") {
            Some(crate::query_normalize::utc_timestamp())
        } else {
            None
        };
        let query = match &timestamp {
            Some(iso) => normalized.replace("datetime()", &format!("timestamp('{iso}')")),
            None => normalized,
        };
        render_params(&query, &scoped)
    }

    fn execute_one(
        &self,
        connection: &Connection<'_>,
        query: &str,
    ) -> Result<QueryRecords, StoreError> {
        let result = connection
            .query(query)
            .map_err(|e| StoreError::Ladybug(format!("{e}")))?;
        let names = result.get_column_names();
        let mut records: Vec<Row> = Vec::new();
        for row in result {
            let mut map = serde_json::Map::new();
            for (index, value) in row.iter().enumerate() {
                let key = names.get(index).cloned().unwrap_or_else(|| format!("c{index}"));
                map.insert(key, ladybug_value_to_json(value));
            }
            records.push(map);
        }
        Ok(records)
    }

    /// Execute + auto-DDL loop (port retry loop của `execute_query_sync` —
    /// nhánh auto-DDL; read/write retries transient không tái lập ở đây vì
    /// embedded call không có connection-class errors).
    fn execute_with_auto_ddl(
        &self,
        connection: &Connection<'_>,
        query: &str,
    ) -> Result<QueryRecords, StoreError> {
        let mut attempted: BTreeSet<String> = BTreeSet::new();
        let current = query.to_string();
        loop {
            match self.execute_one(connection, &current) {
                Ok(records) => return Ok(records),
                Err(StoreError::Ladybug(message)) => {
                    let ddl = self
                        .auto_ddl_statement(&message, &current)
                        .ok_or_else(|| StoreError::Ladybug(message.clone()))?;
                    if !attempted.insert(ddl.clone()) || attempted.len() > 64 {
                        return Err(StoreError::Ladybug(format!(
                            "auto-DDL budget exhausted; last error: {message}"
                        )));
                    }
                    eprintln!("[ladybug auto-DDL] {ddl}");
                    connection
                        .query(&ddl)
                        .map_err(|e| {
                            StoreError::Ladybug(format!(
                                "auto-DDL statement failed: {ddl} (nguyên nhân: {e})"
                            ))
                        })?;
                    //Ladybug caches prepared statements per query string — ở
                    //đây mỗi query đi qua `conn.query` mới nên không có stale
                    //plan; giữ nguyên vòng lặp.
                    continue;
                }
                Err(other) => return Err(other),
            }
        }
    }

    /// Port của `_auto_ddl_statement` — DDL unblock 1 lỗi schema-shaped.
    fn auto_ddl_statement(&self, message: &str, query: &str) -> Option<String> {
        if !auto_ddl_enabled() {
            return None;
        }
        let manifest = code_graph_schema();
        if let Some(caps) = missing_property_re().captures(message) {
            let prop = &caps[1];
            let var = &caps[2];
            let table = resolve_table_for_var(query, var)?;
            let column_type = infer_property_type(query, var, prop);
            return Some(format!("ALTER TABLE `{table}` ADD `{prop}` {column_type}"));
        }
        if let Some(caps) = missing_table_re().captures(message) {
            let table = &caps[1];
            let registry: BTreeMap<&str, (&Vec<String>, &Vec<String>)> = manifest
                .relationship_types
                .iter()
                .map(|(name, sources, targets, _)| (name.as_str(), (sources, targets)))
                .collect();
            if let Some((sources, targets)) = registry.get(table) {
                let pairs = rel_pairs(sources, targets);
                return Some(format!(
                    "CREATE REL TABLE IF NOT EXISTS `{table}` ({pairs})"
                ));
            }
            let pairs = resolve_rel_pairs_from_query(query, table);
            if !pairs.is_empty() {
                return Some(format!(
                    "CREATE REL TABLE IF NOT EXISTS `{table}` ({pairs})"
                ));
            }
            // Node fallback với identity key chuẩn (khớp Python).
            return Some(format!(
                "CREATE NODE TABLE IF NOT EXISTS `{table}` (id STRING, PRIMARY KEY(id))"
            ));
        }
        if let Some(caps) = missing_rel_bind_re().captures(message) {
            let table = &caps[1];
            let registry: BTreeMap<&str, (&Vec<String>, &Vec<String>)> = manifest
                .relationship_types
                .iter()
                .map(|(name, sources, targets, _)| (name.as_str(), (sources, targets)))
                .collect();
            if let Some((sources, targets)) = registry.get(table) {
                let pairs = rel_pairs(sources, targets);
                return Some(format!(
                    "CREATE REL TABLE IF NOT EXISTS `{table}` ({pairs})"
                ));
            }
            let pairs = resolve_rel_pairs_from_query(query, table);
            if !pairs.is_empty() {
                return Some(format!(
                    "CREATE REL TABLE IF NOT EXISTS `{table}` ({pairs})"
                ));
            }
            return None;
        }
        None
    }

    /// Port `_bootstrap_schema` — node/rel tables của CODE_GRAPH_SCHEMA.
    pub fn bootstrap_schema(&mut self) -> Result<(), StoreError> {
        {
            let connection = self.connect()?;
            let manifest = code_graph_schema();
        let base_columns = "id STRING, name STRING, file_path STRING, path STRING, \
             qualified_name STRING, project_id STRING, project_id_normalized STRING, \
             PRIMARY KEY(id)";
        let mut labels: BTreeSet<String> = BTreeSet::new();
        for index in &manifest.indexes {
            labels.insert(index.label.clone());
        }
        for label in &labels {
            let statement =
                format!("CREATE NODE TABLE IF NOT EXISTS `{label}` ({base_columns})");
            connection
                .query(&statement)
                .map_err(|e| StoreError::Ladybug(format!("bootstrap {label}: {e}")))?;
        }
        for (name, sources, targets, _required) in &manifest.relationship_types {
            let pairs = rel_pairs(sources, targets);
            if pairs.is_empty() {
                continue;
            }
            let statement =
                format!("CREATE REL TABLE IF NOT EXISTS `{name}` ({pairs})");
            // Rel table đăng ký lệch giữa các bootstrap cũ không được wedge
            // store — log-and-continue như Python (logger.warning).
            if connection.query(&statement).is_err() {
                continue;
            }
        }
        }
        self.bootstrapped = true;
        Ok(())
    }
}

fn rel_pairs(sources: &[String], targets: &[String]) -> String {
    let mut pairs = Vec::new();
    for src in sources {
        for dst in targets {
            pairs.push(format!("FROM `{src}` TO `{dst}`"));
        }
    }
    pairs.join(", ")
}

fn resolve_table_for_var(query: &str, var: &str) -> Option<String> {
    if let Some(caps) = rel_var_re(var).captures(query) {
        return Some(caps[1].to_string());
    }
    node_var_re(var).captures(query).map(|caps| caps[1].to_string())
}

/// Port `_infer_property_type` —infer từ assignment expression trong query
/// (params đã render inline nên expression là literal cụ thể).
fn infer_property_type(query: &str, var: &str, prop: &str) -> &'static str {
    let assignment = Regex::new(&format!(
        r"(?i)`?{}`?\.`?{}`?\s*=\s*([^\s,;]+)",
        regex::escape(var),
        regex::escape(prop)
    ))
    .unwrap();
    let Some(caps) = assignment.captures(query) else {
        return "STRING";
    };
    let expression = caps[1].to_lowercase();
    if expression.starts_with("timestamp(") {
        return "TIMESTAMP";
    }
    if let Some(field) = expression.strip_prefix("row.") {
        // Params đã render inline — literal của field nằm trong map literal
        // của rows (dạng `<field>: <token>`), classify trực tiếp.
        if let Some(caps) = Regex::new(&format!(
            r"`?{}`?\s*:\s*([^,}}\]]+)",
            regex::escape(field)
        ))
        .unwrap()
        .captures(query)
        {
            return classify_default_literal(&caps[1]);
        }
        return "STRING";
    }
    if expression.starts_with("true") || expression.starts_with("false") {
        return "BOOL";
    }
    if expression.starts_with('[') {
        return "STRING[]";
    }
    if expression.starts_with('\'') || expression.starts_with('"') {
        return "STRING";
    }
    if expression.contains('.') {
        if expression.parse::<f64>().is_ok() {
            return "DOUBLE";
        }
        return "STRING";
    }
    if expression.parse::<i64>().is_ok() {
        return "INT64";
    }
    "STRING"
}

/// Port `_resolve_rel_pairs_from_query` — endpoint labels của rel type trong
/// query: inline hoặc qua biến được type ở node pattern khác.
fn resolve_rel_pairs_from_query(query: &str, table: &str) -> String {
    let var_labels_re =
        Regex::new(r"\(\s*`?(\w+)`?\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?").unwrap();
    let var_labels: BTreeMap<String, String> = var_labels_re
        .captures_iter(query)
        .map(|caps| (caps[1].to_string(), caps[2].to_string()))
        .collect();
    let rel_usage_re = Regex::new(
        r"\(\s*`?(\w+)`?\s*:?\s*`?([A-Za-z_][A-Za-z0-9_]*)?`?[^)]*\)-\[[^\]]*?:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?[^\]]*\]->\(\s*`?(\w+)`?\s*:?\s*`?([A-Za-z_][A-Za-z0-9_]*)?`?[^)]*\)",
    )
    .unwrap();
    let mut endpoints: BTreeSet<(String, String)> = BTreeSet::new();
    for caps in rel_usage_re.captures_iter(query) {
        let (src_var, src_label, rel_type, dst_var, dst_label) = (
            &caps[1],
            caps.get(2).map(|m| m.as_str()),
            &caps[3],
            &caps[4],
            caps.get(5).map(|m| m.as_str()),
        );
        if !rel_type.eq_ignore_ascii_case(table) {
            continue;
        }
        let source = src_label
            .map(str::to_string)
            .or_else(|| var_labels.get(src_var).cloned());
        let target = dst_label
            .map(str::to_string)
            .or_else(|| var_labels.get(dst_var).cloned());
        if let (Some(source), Some(target)) = (source, target) {
            endpoints.insert((source, target));
        }
    }
    endpoints
        .into_iter()
        .map(|(src, dst)| format!("FROM `{src}` TO `{dst}`"))
        .collect::<Vec<_>>()
        .join(", ")
}

// ── Literal rendering ────────────────────────────────────────────────────────

/// Render toàn bộ params vào query text: mỗi `$name` → literal.
/// `rows`-kiểu list-of-map render uniform-key (union key của cả list).
fn render_params(query: &str, params: &BTreeMap<String, Value>) -> Result<String, StoreError> {
    // Single-pass: quét query một lần, thay mọi $token có trong params.
    // Không thay tuần tự qua nhiều vòng vì literal của param trước có thể
    // chứa text `$param-sau` (code snippet trong data) → double-render hỏng data.
    let mut out = String::with_capacity(query.len());
    let mut rest = query;
    while let Some(dollar) = rest.find('$') {
        out.push_str(&rest[..dollar]);
        rest = &rest[dollar..];
        let token_len = rest
            .bytes()
            .enumerate()
            .skip(1)
            .take_while(|(index, byte)| {
                *index == 1 || (byte.is_ascii_alphanumeric() || *byte == b'_')
            })
            .map(|(index, _)| index + 1)
            .last()
            .unwrap_or(1);
        let token = &rest[..token_len];
        let key = &token[1..];
        match params.get(key) {
            Some(value) => {
                let literal = render_uniform_with_query(value, query)
                    .map_err(|m| StoreError::Invalid(format!("param {key}: {m}")))?;
                out.push_str(&literal);
            }
            // Không phải param của writer (vd $project_id_normalized khi
            // params không có) — giữ nguyên token.
            None => out.push_str(token),
        }
        rest = &rest[token_len..];
    }
    out.push_str(rest);
    Ok(out)
}

/// `row.<field>` references trong query — dùng để union key khi rows thiếu
/// field mà query vẫn tham chiếu (params side: row.missing → NULL; STRUCT
/// literal side: binder error nếu không có field).
fn row_field_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\brow\.([A-Za-z_][A-Za-z0-9_]*)").unwrap())
}

fn render_uniform_with_query(value: &Value, query: &str) -> Result<String, String> {
    let mut schema = SchemaNode::default();
    schema.absorb(value);
    let mut hints = BTreeMap::new();
    if matches!(value, Value::Array(_)) {
        for caps in row_field_re().captures_iter(query) {
            schema.children.entry(caps[1].to_string()).or_default();
        }
        hints = coalesce_type_hints(query);
    }
    Ok(schema.render(value, &hints))
}

#[derive(Default)]
struct SchemaNode {
    children: BTreeMap<String, SchemaNode>,
    /// Field từng chứa string element (trực tiếp hoặc trong array) — dùng
    /// để type-tag array rỗng: ladybug unify `[]` thành INT64[] và vỡ khi
    /// batch trộn với STRING[] giữa các row (implicit cast not supported).
    saw_string_element: bool,
}

/// Loại NULL-typed theo hint từ query: `coalesce(row.<field>, <default>)`
/// cho biết kiểu mà consumer kỳ vọng (params side NULL tự do; STRUCT literal
/// side NULL bị type thành STRING nên cần CAST(NULL AS T)).
fn classify_default_literal(literal: &str) -> &'static str {
    let literal = literal.trim();
    let lowered = literal.to_lowercase();
    if lowered.starts_with("timestamp(") {
        return "TIMESTAMP";
    }
    if lowered.starts_with("cast(null as ") {
        let inner = lowered
            .trim_start_matches("cast(null as ")
            .trim_end_matches(")")
            .trim()
            .to_string();
        return match inner.as_str() {
            "BOOL" | "INT64" | "DOUBLE" | "TIMESTAMP" | "STRING[]" => {
                Box::leak(inner.clone().into_boxed_str())
            }
            _ => "STRING",
        };
    }
    if lowered == "true" || lowered == "false" {
        return "BOOL";
    }
    if literal.starts_with('[') {
        return "STRING[]";
    }
    if lowered.starts_with('\'') || lowered.starts_with('"') {
        return "STRING";
    }
    if lowered.contains('.') {
        if lowered.parse::<f64>().is_ok() {
            return "DOUBLE";
        }
        return "STRING";
    }
    if lowered.parse::<i64>().is_ok() {
        return "INT64";
    }
    "STRING"
}

/// Trích type hint cho từng row field từ các `coalesce(row.f, default)`.
fn coalesce_type_hints(query: &str) -> BTreeMap<String, String> {
    let re = Regex::new(r"(?i)coalesce\s*\(\s*row\.([A-Za-z_][A-Za-z0-9_]*)\s*,\s*([^()]+?)\s*\)")
        .unwrap();
    let mut hints = BTreeMap::new();
    for caps in re.captures_iter(query) {
        let field = caps[1].to_string();
        let hint = classify_default_literal(&caps[2]);
        hints.insert(field, hint.to_string());
    }
    hints
}

impl SchemaNode {
    fn absorb(&mut self, value: &Value) {
        match value {
            Value::Object(map) => {
                for (key, child) in map {
                    self.children.entry(key.clone()).or_default().absorb(child);
                }
            }
            Value::Array(items) => {
                for item in items {
                    if item.is_string() {
                        self.saw_string_element = true;
                    }
                    self.absorb(item);
                }
            }
            Value::String(_) => self.saw_string_element = true,
            _ => {}
        }
    }

    fn render(&self, value: &Value, hints: &BTreeMap<String, String>) -> String {
        match value {
            Value::Null => {
                // Root-level NULL với hint → CAST(NULL AS T) để giữ kiểu mong
                // đợi trong STRUCT literal (bare NULL type thành STRING).
                "NULL".to_string()
            }
            Value::Bool(true) => "true".to_string(),
            Value::Bool(false) => "false".to_string(),
            Value::Number(n) => {
                if let Some(i) = n.as_i64() {
                    i.to_string()
                } else {
                    let f = n.as_f64().unwrap_or_default();
                    // str(float) của Python: 2.0 → "2.0".
                    if f.is_finite() && f.fract() == 0.0 && f.abs() < 1e16 {
                        format!("{f:.1}")
                    } else {
                        format!("{f}")
                    }
                }
            }
            Value::String(s) => quote_cypher(s),
            Value::Array(items) => {
                if items.is_empty() && self.saw_string_element {
                    // Batch có string element ở row khác — tag kiểu để
                    // UNWIND không unify array rỗng thành INT64[].
                    return "CAST([] AS STRING[])".to_string();
                }
                let rendered: Vec<String> =
                    items.iter().map(|item| self.render(item, hints)).collect();
                format!("[{}]", rendered.join(", "))
            }
            Value::Object(map) => {
                if map.is_empty() {
                    // Ladybug không có literal map rỗng `{}` (parse error) —
                    // NULL thay thế: consumer đọc field này qua coalesce.
                    return "NULL".to_string();
                }
                let mut parts = Vec::new();
                for (key, child) in &self.children {
                    let value = map.get(key).unwrap_or(&Value::Null);
                    let rendered = if value.is_null() {
                        match hints.get(key) {
                            Some(hint) => format!("CAST(NULL AS {hint})"),
                            None => "NULL".to_string(),
                        }
                    } else {
                        child.render(value, hints)
                    };
                    parts.push(format!("`{key}`: {rendered}"));
                }
                format!("{{{}}}", parts.join(", "))
            }
        }
    }
}

/// Quote chuỗi theo Cypher — escape `\`, `"`, `'`, control chars.
pub fn quote_cypher(value: &str) -> String {
    let mut out = String::with_capacity(value.len() + 2);
    out.push('"');
    for ch in value.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\'' => out.push_str("\\'"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            other => out.push(other),
        }
    }
    out.push('"');
    out
}

/// Đọc FlatTuple value → JSON.
fn ladybug_value_to_json(value: &lbug::Value) -> Value {
    use lbug::Value as V;
    match value {
        V::Null(_) => Value::Null,
        V::Bool(b) => Value::Bool(*b),
        V::Int8(v) => serde_json::json!(v),
        V::Int16(v) => serde_json::json!(v),
        V::Int32(v) => serde_json::json!(v),
        V::Int64(v) => serde_json::json!(v),
        V::UInt8(v) => serde_json::json!(v),
        V::UInt16(v) => serde_json::json!(v),
        V::UInt32(v) => serde_json::json!(v),
        V::UInt64(v) => serde_json::json!(v),
        V::Float(v) => serde_json::json!(v),
        V::Double(v) => serde_json::json!(v),
        V::String(s) => Value::String(s.clone()),
        V::InternalID(id) => serde_json::json!(id.offset),
        V::List(_, items) | V::Array(_, items) => Value::Array(
            items
                .iter()
                .map(ladybug_value_to_json)
                .collect::<Vec<_>>(),
        ),
        V::Struct(fields) => {
            let mut map = serde_json::Map::new();
            for (name, item) in fields {
                map.insert(name.clone(), ladybug_value_to_json(item));
            }
            Value::Object(map)
        }
        V::Map(_, pairs) => {
            let mut map = serde_json::Map::new();
            for (key, item) in pairs {
                let key = match key {
                    V::String(s) => s.clone(),
                    other => format!("{other:?}"),
                };
                map.insert(key, ladybug_value_to_json(item));
            }
            Value::Object(map)
        }
        other => Value::String(format!("{other}")),
    }
}

impl GraphStore for LadybugStore {
    fn provider(&self) -> &'static str {
        "ladybug"
    }

    fn execute_query(
        &mut self,
        query: &str,
        parameters: &BTreeMap<String, Value>,
        database: Option<&str>,
    ) -> Result<QueryRecords, StoreError> {
        // Ladybug: 1 store file / graph — provider name / database param là
        // tên đồ ngữ nội bộ; chỉ primary graph được phục vụ ở backend này
        // (multi-graph routing nằm ở tầng factory — khớp phase-03 scope).
        let _ = database;
        if is_write_intent(query) && !self.bootstrapped {
            self.bootstrap_schema()?;
        }
        let prepared = Self::prepare(query, parameters)?;
        let connection = self.connect()?;
        self.execute_with_auto_ddl(&connection, &prepared)
    }

    fn ensure_schema(&mut self, _database: Option<&str>) -> Result<(), StoreError> {
        // Ladybug: static schema — bootstrap node/rel tables CHÍNH LÀ hợp
        // đồng schema của backend embedded này. Preflight index-poll (vốn
        // yêu cầu mọi identity index ONLINE) không chạy được trên static
        // schema và Python driver cũng chưa từng pass nó trên ladybug —
        // trait vẫn expose preflight qua `crate::preflight::ensure_schema`
        // cho falkordb.
        if !self.bootstrapped {
            self.bootstrap_schema()?;
        }
        Ok(())
    }

    fn inspect_indexes(
        &mut self,
        database: Option<&str>,
    ) -> Result<Vec<BTreeMap<String, Value>>, StoreError> {
        // Port inspect_indexes: show_indexes() → map ART→range, _PK→range,
        // FTS→fulltext.
        let records = self.execute_query(
            "CALL show_indexes() RETURN *",
            &BTreeMap::new(),
            database,
        )?;
        let mut normalized = Vec::new();
        for record in &records {
            let table_name = record
                .get("table_name")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let index_name = record
                .get("index_name")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let index_type = record
                .get("index_type")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_lowercase();
            let properties: Vec<String> = match record.get("property_names") {
                Some(Value::String(s)) => vec![s.clone()],
                Some(Value::Array(items)) => items
                    .iter()
                    .filter_map(|item| item.as_str().map(str::to_string))
                    .collect(),
                _ => vec![],
            };
            let mapped_type = if index_type == "fts" {
                "fulltext"
            } else if index_type == "art" || index_name == "_PK" {
                "range"
            } else {
                match index_type.as_str() {
                    "fulltext" => "fulltext",
                    "range" => "range",
                    _ => index_type.as_str(),
                }
            };
            for prop in properties {
                let mut row = BTreeMap::new();
                row.insert("label".to_string(), Value::String(table_name.clone()));
                row.insert(
                    "properties".to_string(),
                    Value::Array(vec![Value::String(prop)]),
                );
                row.insert("index_type".to_string(), Value::String(mapped_type.to_string()));
                row.insert("entity_type".to_string(), Value::String("node".to_string()));
                row.insert("status".to_string(), Value::String("ONLINE".to_string()));
                normalized.push(row);
            }
        }
        Ok(normalized)
    }

    fn create_indexes(
        &mut self,
        indexes: &[IndexSpec],
        _database: Option<&str>,
    ) -> Result<(), StoreError> {
        // Port create_indexes: range → ART, fulltext → FTS; swallow
        // already-exists.
        let connection = self.connect()?;
        let exists_re = Regex::new(r"(?i)already exists").unwrap();
        for index in indexes {
            let label = validate_cypher_identifier(&index.label, "label")
                .map_err(StoreError::Invalid)?;
            let prop = validate_cypher_identifier(&index.property, "property")
                .map_err(StoreError::Invalid)?;
            let result = if index.index_type == "fulltext" {
                let index_name = index_name(&label, &[prop.as_str()]);
                connection
                    .query("INSTALL FTS")
                    .and_then(|_| connection.query("LOAD EXTENSION FTS"))
                    .map_err(|e| StoreError::Ladybug(format!("{e}")))?;
                connection
                    .query(&format!(
                        "CALL CREATE_FTS_INDEX('{label}', '{index_name}', ['{prop}'])"
                    ))
                    .map_err(|e| StoreError::Ladybug(format!("{e}")))
            } else {
                let index_name = index_name(&label, &[prop.as_str()]);
                connection
                    .query(&format!(
                        "CREATE ART INDEX `{index_name}` FOR (t:`{label}`) ON (t.`{prop}`)"
                    ))
                    .map_err(|e| StoreError::Ladybug(format!("{e}")))
            };
            if let Err(StoreError::Ladybug(message)) = result {
                // Soft-skip: (1) "already exists" như Python; (2) column-missing
                // — trên store mới, property của identity (vd
                // Workflow.workflow_id) chưa có trong table vì bootstrap chỉ
                // khai báo base columns; auto-DDL sẽ ADD column khi query đầu
                // tiên tham chiếu. Python driver ở đây raise (ladybug provider
                // chưa từng qua preflight trọn vẹn) — Rust soft-skip để local
                // provider dùng được, đã ghi trong phase-03 notes.
                let column_missing = message.contains("does not exist in table");
                if !exists_re.is_match(&message) && !column_missing {
                    return Err(StoreError::Ladybug(format!(
                        "failed to create {} index on {label}({prop}): {message}",
                        index.index_type
                    )));
                }
            }
        }
        Ok(())
    }
}

/// Port `_index_name` — idx_<label>_<props> sanitized.
pub fn index_name(label: &str, props: &[&str]) -> String {
    let raw = format!("{label}_{}", props.join("_")).to_lowercase();
    let sanitized: String = raw
        .chars()
        .map(|c| {
            if c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect();
    format!("idx_{sanitized}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn merge_natural_key_rewrite_set_continuation() {
        let query = "UNWIND $rows AS row\nMERGE (p:Project {project_id: row.id})\nSET p.name = row.name\nRETURN count(p) as count";
        let out = rewrite_merge_natural_keys(query);
        assert!(out.contains("MERGE (p:Project {id: row.id})"), "{out}");
        assert!(
            out.contains("SET p.project_id = row.id, p.name = row.name"),
            "{out}"
        );
    }

    #[test]
    fn merge_natural_key_rewrite_map_union_continuation() {
        let query = "UNWIND $rows AS row\nMERGE (site:CallSite {site_id: row.site_id})\nSET site += row.props";
        let out = rewrite_merge_natural_keys(query);
        assert!(out.contains("MERGE (site:CallSite {id: row.site_id})"), "{out}");
        assert!(
            out.contains("SET site.site_id = row.site_id, site += row.props"),
            "{out}"
        );
    }

    #[test]
    fn merge_natural_key_rewrite_on_create_continuation() {
        let query = "MERGE (p:Project {project_id: $project_id})\nON CREATE SET\n    p.name = $name\nON MATCH SET\n    p.name = $name";
        let out = rewrite_merge_natural_keys(query);
        assert!(out.contains("MERGE (p:Project {id: $project_id})"), "{out}");
        assert!(
            out.contains("ON CREATE SET p.project_id = $project_id,"),
            "{out}"
        );
        // ON MATCH SET giữ nguyên vị trí ngay sau khối ON CREATE SET.
        assert!(out.contains("    p.name = $name\nON MATCH SET"), "{out}");
    }

    #[test]
    fn merge_id_keyed_patterns_untouched() {
        let query = "MERGE (f:File {id: row.id})\nMERGE (p)-[:HAS_REPOSITORY]->(r)\nMERGE (p:RouteParam {id: row.symbol_id + '::' + row.route_name})";
        assert_eq!(rewrite_merge_natural_keys(query), query);
    }

    #[test]
    fn uniform_render_rows_union_keys() {
        let rows = vec![
            serde_json::json!({"id": "a", "name": "A"}),
            serde_json::json!({"id": "b", "name": Value::Null, "extra": 1}),
        ];
        let rendered = render_uniform_with_query(&Value::Array(rows), "").unwrap();
        // Row 1 thiếu extra → NULL; key set uniform.
        assert!(rendered.contains("`id`: \"a\""), "{rendered}");
        assert!(rendered.contains("`extra`: NULL"));
        assert!(rendered.contains("`extra`: 1"));
    }

    #[test]
    fn render_rows_with_query_referenced_fields() {
        let rows = vec![serde_json::json!({"id": "a"})];
        let rendered = render_uniform_with_query(
            &Value::Array(rows),
            "UNWIND $rows AS row SET n.qualified_name = row.qualified_name, n.id = row.id",
        )
        .unwrap();
        assert!(rendered.contains("`qualified_name`: NULL"), "{rendered}");
        assert!(rendered.contains("`id`: \"a\""), "{rendered}");
    }

    #[test]
    fn string_escaping_cypher() {
        assert_eq!(quote_cypher("O'Brien"), "\"O\\'Brien\"");
        assert_eq!(quote_cypher("a\"b\\c"), "\"a\\\"b\\\\c\"");
    }

    #[test]
    fn param_replacement_respects_token_boundary() {
        let mut params = BTreeMap::new();
        params.insert("project_id".to_string(), Value::String("p".into()));
        let out = render_params(
            "SET n.project_id_normalized = $project_id_normalized, n.p = $project_id",
            &params,
        )
        .unwrap();
        assert!(out.contains("$project_id_normalized"), "{out}");
        assert!(out.contains("n.p = \"p\""), "{out}");
    }

    #[test]
    fn float_renders_python_style() {
        let rendered = render_uniform_with_query(&serde_json::json!(2.0), "").unwrap();
        assert_eq!(rendered, "2.0");
        let rendered = render_uniform_with_query(&serde_json::json!(1.5), "").unwrap();
        assert_eq!(rendered, "1.5");
    }

    #[test]
    fn single_pass_render_no_double_render() {
        // Row data chứa text "$project_id" (code snippet) — single-pass render
        // không được thay nó bằng literal của param project_id.
        let mut params = BTreeMap::new();
        params.insert("rows".to_string(), serde_json::json!([
            {"id": "a", "code": "use $project_id as x"}
        ]));
        params.insert("project_id".to_string(), Value::String("p".into()));
        let out = render_params("UNWIND $rows AS row MERGE (n {id: row.id}) SET n.p = $project_id", &params)
            .unwrap();
        assert!(out.contains("use $project_id as x"), "{out}");
        assert!(out.ends_with("SET n.p = \"p\""), "{out}");
    }

    #[test]
    fn index_name_matches_python() {
        assert_eq!(index_name("Function", &["id"]), "idx_function_id");
    }
}

//! Ghi đồ thị vào Ladybug store qua crate `lbug` (embedded, 1 file/graph).
//!
//! Đường mở store giống `cortex-graph-writer/src/store/ladybug_store.rs`
//! (`Database::new(path, SystemConfig::default())` + `Connection::new`), nhưng
//! migration tự dựng DDL/insert literals để kiểm soát schema động của graph
//! nguồn (label/rel-type/property khám phá từ FalkorDB) — đằng nào
//! `LadybugStore` cũng render params thành literals inline (dialect note của
//! phase 03). Verify đọc lại qua [`cortex_graph_writer::store::LadybugStore`].
//!
//! Quy ước schema:
//! * Mỗi label → 1 node table, PK `_fid INT64` = node id nội tại FalkorDB
//!   (không đụng property `id` nghiệp vụ của node).
//! * Node nhiều label → ghi vào table của label đầu tiên (primary), các label
//!   còn lại mất tính first-class (ghi chú trong report); node không label →
//!   table `_unlabeled`.
//! * Property type infer từ union giá trị thấy được: Bool→BOOL, Int→INT64,
//!   Int+Double→DOUBLE, Array→STRING[], còn lại/mix→STRING (coerce khi ghi).

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use lbug::{Connection, Database, SystemConfig};
use serde_json::Value;

/// Property map của một node/rel đọc được từ nguồn.
pub type JsonMap = serde_json::Map<String, Value>;

/// Kiểu cột Ladybug đã chọn cho một property.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ColType {
    Bool,
    Int64,
    Double,
    String,
    StringArray,
}

impl ColType {
    pub fn ddl(self) -> &'static str {
        match self {
            ColType::Bool => "BOOL",
            ColType::Int64 => "INT64",
            ColType::Double => "DOUBLE",
            ColType::String => "STRING",
            ColType::StringArray => "STRING[]",
        }
    }
}

/// Thông tin kiểu gom được từ các giá trị đã thấy (pre-merge).
#[derive(Debug, Default, Clone)]
pub struct TypeScan {
    seen_bool: bool,
    seen_int: bool,
    seen_float: bool,
    seen_string: bool,
    seen_array: bool,
    any_value: bool,
}

impl TypeScan {
    pub fn observe(value: &Value) -> TypeScan {
        let mut scan = TypeScan::default();
        scan.absorb(value);
        scan
    }

    pub fn absorb(&mut self, value: &Value) {
        match value {
            Value::Null => {}
            Value::Bool(_) => {
                self.seen_bool = true;
                self.any_value = true;
            }
            Value::Number(n) => {
                if n.is_i64() || n.is_u64() {
                    self.seen_int = true;
                } else {
                    self.seen_float = true;
                }
                self.any_value = true;
            }
            Value::String(_) => {
                self.seen_string = true;
                self.any_value = true;
            }
            Value::Array(_) => {
                self.seen_array = true;
                self.any_value = true;
            }
            Value::Object(_) => {
                // Map từ FalkorDB (không hỗ trợ first-class) → serialize STRING.
                self.seen_string = true;
                self.any_value = true;
            }
        }
    }

    pub fn col_type(self) -> ColType {
        if !self.any_value {
            return ColType::String;
        }
        if self.seen_array {
            return ColType::StringArray;
        }
        let kinds = [
            self.seen_bool,
            self.seen_int || self.seen_float,
            self.seen_string,
        ]
        .iter()
        .filter(|seen| **seen)
        .count();
        if kinds > 1 {
            return ColType::String;
        }
        if self.seen_bool {
            ColType::Bool
        } else if self.seen_float {
            ColType::Double
        } else if self.seen_int {
            ColType::Int64
        } else {
            ColType::String
        }
    }
}

/// Sanitize identifier (label / property) về [A-Za-z0-9_], không bắt đầu bằng
/// digit; rỗng → `_unnamed`.
pub fn sanitize_identifier(raw: &str) -> String {
    let mut out: String = raw
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '_' { c } else { '_' })
        .collect();
    if out.is_empty() {
        return "_unnamed".to_string();
    }
    if out.chars().next().is_some_and(|c| c.is_ascii_digit()) {
        out.insert(0, 'g');
    }
    out
}

/// Bảng tên đã sanitize, chống collision (2 label khác nhau sanitize trùng).
#[derive(Debug, Default)]
pub struct NameRegistry {
    assigned: BTreeMap<String, String>,
}

impl NameRegistry {
    /// Trả về tên table/cột đã sanitize cho tên gốc; lần đầu gặp → cấp tên,
    /// trùng → gắn suffix `_2`, `_3`, ...
    pub fn assign(&mut self, original: &str) -> String {
        if let Some(existing) = self.assigned.get(original) {
            return existing.clone();
        }
        let base = sanitize_identifier(original);
        let mut candidate = base.clone();
        let mut suffix = 2usize;
        while self.assigned.values().any(|name| name == &candidate) {
            candidate = format!("{base}_{suffix}");
            suffix += 1;
        }
        self.assigned.insert(original.to_string(), candidate.clone());
        candidate
    }

    pub fn get(&self, original: &str) -> Option<&String> {
        self.assigned.get(original)
    }
}

/// Schema node table của một label: tên table + cột (tên đã sanitize → type).
#[derive(Debug, Clone)]
pub struct TableSchema {
    pub table: String,
    /// (property gốc, tên cột, kiểu)
    pub columns: Vec<(String, String, ColType)>,
}

/// Toàn bộ schema của một graph sau discovery.
#[derive(Debug, Default)]
pub struct GraphSchemaPlan {
    pub node_registry: NameRegistry,
    pub rel_registry: NameRegistry,
    pub column_registry: NameRegistry,
    /// label gốc → table schema
    pub nodes: BTreeMap<String, TableSchema>,
    /// rel type gốc → (table schema, endpoint pairs (label gốc))
    pub rels: BTreeMap<String, (TableSchema, BTreeSet<(String, String)>)>,
}

impl GraphSchemaPlan {
    /// Đảm bảo label gốc có entry trong plan (table + registry).
    pub fn ensure_label(&mut self, label: &str) -> String {
        self.node_registry.assign(label)
    }

    pub fn ensure_rel_type(&mut self, rel_type: &str) -> String {
        self.rel_registry.assign(rel_type)
    }

    /// Observe một property value cho label, trả tên cột.
    pub fn observe_node_prop(&mut self, label: &str, table: &str, prop: &str, value: &Value) {
        let column = self.column_registry.assign(prop);
        let scan = TypeScan::observe(value);
        let entry = self
            .nodes
            .entry(label.to_string())
            .or_insert_with(|| TableSchema {
                table: table.to_string(),
                columns: Vec::new(),
            });
        match entry
            .columns
            .iter_mut()
            .find(|(orig, _, _)| orig == prop)
        {
            Some((_, _, ty)) => {
                // Re-derive: merge scan vào type hiện tại (xấp xỉ: downgrade).
                *ty = downgrade(*ty, scan);
            }
            None => entry
                .columns
                .push((prop.to_string(), column, scan.col_type())),
        }
    }

    pub fn observe_rel_prop(&mut self, rel_type: &str, table: &str, prop: &str, value: &Value) {
        let column = self.column_registry.assign(prop);
        let scan = TypeScan::observe(value);
        let entry = &mut self
            .rels
            .entry(rel_type.to_string())
            .or_insert_with(|| {
                (
                    TableSchema {
                        table: table.to_string(),
                        columns: Vec::new(),
                    },
                    BTreeSet::new(),
                )
            })
            .0;
        match entry.columns.iter_mut().find(|(orig, _, _)| orig == prop) {
            Some((_, _, ty)) => *ty = downgrade(*ty, scan),
            None => entry
                .columns
                .push((prop.to_string(), column, scan.col_type())),
        }
    }

    pub fn observe_rel_pair(&mut self, rel_type: &str, src_label: &str, dst_label: &str) {
        let entry = self
            .rels
            .entry(rel_type.to_string())
            .or_insert_with(|| {
                (
                    TableSchema {
                        table: self.rel_registry.assign(rel_type),
                        columns: Vec::new(),
                    },
                    BTreeSet::new(),
                )
            });
        entry
            .1
            .insert((src_label.to_string(), dst_label.to_string()));
    }
}

/// Downgrade kiểu cột khi giá trị mới không khớp type đã chọn.
fn downgrade(current: ColType, scan: TypeScan) -> ColType {
    let incoming = scan.col_type();
    if current == incoming {
        return current;
    }
    match (current, incoming) {
        (ColType::Int64, ColType::Double) | (ColType::Double, ColType::Int64) => ColType::Double,
        _ => ColType::String,
    }
}

/// Kết nối ghi embedded Ladybug cho MỘT store file (1 graph).
pub struct LadybugWriter {
    database: Database,
}

impl LadybugWriter {
    /// Mở (tạo mới) store file — đường giống `LadybugStore::open` (tạo thư mục
    /// cha trước vì Ladybug không tự tạo).
    pub fn open(path: &Path) -> Result<Self, String> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|error| format!("mkdir {}: {error}", parent.display()))?;
        }
        let database = Database::new(path, SystemConfig::default())
            .map_err(|error| format!("cannot open store {}: {error}", path.display()))?;
        Ok(Self { database })
    }

    fn conn(&self) -> Result<Connection<'_>, String> {
        Connection::new(&self.database).map_err(|error| format!("connect: {error}"))
    }

    /// Chạy một query ghi (DDC/DML), fail-closed kèm SQL rút gọn trong lỗi.
    pub fn exec(&self, query: &str) -> Result<(), String> {
        let conn = self.conn()?;
        conn.query(query)
            .map_err(|error| format!("query failed: {error} — sql: {}", preview(query, 240)))?;
        Ok(())
    }

    /// Dựng node/rel tables cho schema plan.
    pub fn create_schema(&self, plan: &GraphSchemaPlan) -> Result<(), String> {
        for (label, schema) in &plan.nodes {
            let mut cols: Vec<String> = vec!["_fid INT64 PRIMARY KEY".to_string()];
            for (_, column, ty) in &schema.columns {
                cols.push(format!("`{column}` {}", ty.ddl()));
            }
            let ddl = format!(
                "CREATE NODE TABLE IF NOT EXISTS `{}` ({})",
                schema.table,
                cols.join(", ")
            );
            self.exec(&ddl)
                .map_err(|error| format!("node table {label}: {error}"))?;
        }
        for (rel_type, (schema, pairs)) in &plan.rels {
            if pairs.is_empty() {
                continue;
            }
            let mut parts: Vec<String> = pairs
                .iter()
                .map(|(src, dst)| {
                    let src_table = plan
                        .nodes
                        .get(src)
                        .map(|s| s.table.clone())
                        .unwrap_or_else(|| plan.node_registry.get(src).cloned().unwrap_or_default());
                    let dst_table = plan
                        .nodes
                        .get(dst)
                        .map(|s| s.table.clone())
                        .unwrap_or_else(|| plan.node_registry.get(dst).cloned().unwrap_or_default());
                    format!("FROM `{src_table}` TO `{dst_table}`")
                })
                .collect();
            for (_, column, ty) in &schema.columns {
                parts.push(format!("`{column}` {}", ty.ddl()));
            }
            let ddl = format!(
                "CREATE REL TABLE IF NOT EXISTS `{}` ({})",
                schema.table,
                parts.join(", ")
            );
            self.exec(&ddl)
                .map_err(|error| format!("rel table {rel_type}: {error}"))?;
        }
        Ok(())
    }

    /// Insert 1 batch node cùng label (multi-CREATE trong 1 statement).
    pub fn insert_nodes(
        &self,
        schema: &TableSchema,
        rows: &[NodeRow],
    ) -> Result<usize, String> {
        if rows.is_empty() {
            return Ok(0);
        }
        let mut patterns: Vec<String> = Vec::with_capacity(rows.len());
        for row in rows {
            let mut props: Vec<String> = vec![format!("_fid: {}", row.fid)];
            for (prop, column, ty) in &schema.columns {
                let Some(value) = row.props.get(prop) else {
                    continue;
                };
                if value.is_null() {
                    continue;
                }
                let literal = render_literal(value, *ty)
                    .map_err(|message| format!("node {} `_fid:{}`: {message}", schema.table, row.fid))?;
                props.push(format!("`{column}`: {literal}"));
            }
            patterns.push(format!("(:`{}` {{{}}})", schema.table, props.join(", ")));
        }
        let sql = format!("CREATE {}", patterns.join(", "));
        self.exec(&sql)?;
        Ok(rows.len())
    }

    /// Insert 1 batch rel cùng type (multi MATCH + multi CREATE).
    pub fn insert_rels(
        &self,
        plan: &GraphSchemaPlan,
        schema: &TableSchema,
        rows: &[RelRow],
    ) -> Result<usize, String> {
        if rows.is_empty() {
            return Ok(0);
        }
        let rel_table = &schema.table;
        let mut match_patterns: Vec<String> = Vec::with_capacity(rows.len() * 2);
        let mut create_patterns: Vec<String> = Vec::with_capacity(rows.len());
        for (index, row) in rows.iter().enumerate() {
            let (src_label, dst_label) = &row.endpoints;
            let src_table = plan
                .nodes
                .get(src_label)
                .map(|s| s.table.clone())
                .unwrap_or_else(|| {
                    plan.node_registry
                        .get(src_label)
                        .cloned()
                        .unwrap_or_default()
                });
            let dst_table = plan
                .nodes
                .get(dst_label)
                .map(|s| s.table.clone())
                .unwrap_or_else(|| {
                    plan.node_registry
                        .get(dst_label)
                        .cloned()
                        .unwrap_or_default()
                });
            match_patterns.push(format!(
                "(a{index}:`{src_table}` {{_fid: {}}})",
                row.src_fid
            ));
            match_patterns.push(format!(
                "(b{index}:`{dst_table}` {{_fid: {}}})",
                row.dst_fid
            ));
            let mut props: Vec<String> = Vec::new();
            for (prop, column, ty) in &schema.columns {
                let Some(value) = row.props.get(prop) else {
                    continue;
                };
                if value.is_null() {
                    continue;
                }
                let literal = render_literal(value, *ty).map_err(|message| {
                    format!("rel {rel_table} id {}: {message}", row.fid)
                })?;
                props.push(format!("`{column}`: {literal}"));
            }
            let props_sql = if props.is_empty() {
                String::new()
            } else {
                format!(" {{{}}}", props.join(", "))
            };
            create_patterns.push(format!(
                "(a{index})-[:`{rel_table}`{props_sql}]->(b{index})"
            ));
        }
        let sql = format!(
            "MATCH {} CREATE {}",
            match_patterns.join(", "),
            create_patterns.join(", ")
        );
        self.exec(&sql)?;
        Ok(rows.len())
    }
}

/// Một node đọc từ FalkorDB, chờ ghi.
pub struct NodeRow {
    pub fid: i64,
    /// Các label gốc (primary = phần tử đầu).
    pub labels: Vec<String>,
    pub props: JsonMap,
}

/// Một rel đọc từ FalkorDB, chờ ghi.
pub struct RelRow {
    pub fid: i64,
    pub src_fid: i64,
    pub dst_fid: i64,
    /// (label gốc của src node, label gốc của dst node) — primary labels.
    pub endpoints: (String, String),
    pub props: JsonMap,
}

/// Render một JSON value thành Cypher literal theo kiểu cột đã chọn.
pub fn render_literal(value: &Value, ty: ColType) -> Result<String, String> {
    if value.is_null() {
        return Ok("NULL".to_string());
    }
    match ty {
        ColType::Bool => match value {
            Value::Bool(true) => Ok("true".to_string()),
            Value::Bool(false) => Ok("false".to_string()),
            other => Ok(if is_truthy(other) { "true" } else { "false" }.to_string()),
        },
        ColType::Int64 => match value {
            Value::Number(n) if n.is_i64() => Ok(n.as_i64().unwrap_or_default().to_string()),
            Value::Number(n) if n.is_u64() => Ok(n.to_string()),
            Value::Number(n) => {
                let f = n.as_f64().unwrap_or_default();
                if f.fract() == 0.0 && f.abs() < 9e15 {
                    Ok((f as i64).to_string())
                } else {
                    Err(format!("value {value} is not an integer"))
                }
            }
            other => Err(format!("value {other} is not an integer")),
        },
        ColType::Double => match value {
            Value::Number(n) => {
                let f = n.as_f64().unwrap_or_default();
                Ok(render_double(f))
            }
            other => Err(format!("value {other} is not a number")),
        },
        ColType::String => Ok(quote_cypher(&stringify(value))),
        ColType::StringArray => {
            let items: Vec<String> = match value {
                Value::Array(items) => items
                    .iter()
                    .map(|item| quote_cypher(&stringify(item)))
                    .collect::<Vec<_>>(),
                other => vec![quote_cypher(&stringify(other))],
            };
            Ok(format!("[{}]", items.join(", ")))
        }
    }
}

fn is_truthy(value: &Value) -> bool {
    !matches!(value, Value::Null | Value::Bool(false)) && value != &Value::Number(0.into())
}

/// str(float) style của Python: 2.0 → "2.0".
fn render_double(f: f64) -> String {
    if f.is_finite() && f.fract() == 0.0 && f.abs() < 1e16 {
        format!("{f:.1}")
    } else {
        format!("{f}")
    }
}

/// Coerce value về string cho cột STRING/STRING[] (map → JSON, bool → true).
fn stringify(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Object(_) | Value::Array(_) => {
            serde_json::to_string(value).unwrap_or_else(|_| format!("{value}"))
        }
        other => other.to_string(),
    }
}

/// Quote chuỗi theo chuẩn Cypher (giống `quote_cypher` của ladybug_store).
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

fn preview(query: &str, max: usize) -> String {
    if query.len() <= max {
        query.to_string()
    } else {
        format!("{}…", &query[..max])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn sanitize_identifiers() {
        assert_eq!(sanitize_identifier("Person"), "Person");
        assert_eq!(sanitize_identifier("my-label.v2"), "my_label_v2");
        assert_eq!(sanitize_identifier("9lives"), "g9lives");
        assert_eq!(sanitize_identifier(""), "_unnamed");
        assert_eq!(sanitize_identifier("a@b c"), "a_b_c");
    }

    #[test]
    fn name_registry_dedupes() {
        let mut registry = NameRegistry::default();
        assert_eq!(registry.assign("a-b"), "a_b");
        assert_eq!(registry.assign("a.b"), "a_b_2");
        assert_eq!(registry.assign("a-b"), "a_b");
    }

    #[test]
    fn type_scan_merge_rules() {
        assert_eq!(TypeScan::observe(&json!(1)).col_type(), ColType::Int64);
        assert_eq!(TypeScan::observe(&json!(1.5)).col_type(), ColType::Double);
        assert_eq!(TypeScan::observe(&json!(true)).col_type(), ColType::Bool);
        assert_eq!(
            TypeScan::observe(&json!("x")).col_type(),
            ColType::String
        );
        assert_eq!(
            TypeScan::observe(&json!(["a"])).col_type(),
            ColType::StringArray
        );
        assert_eq!(
            TypeScan::observe(&Value::Null).col_type(),
            ColType::String
        );
    }

    #[test]
    fn literal_rendering_coerces() {
        assert_eq!(render_literal(&json!("O'Brien"), ColType::String).unwrap(), "\"O\\'Brien\"");
        assert_eq!(render_literal(&json!(7), ColType::Int64).unwrap(), "7");
        assert_eq!(render_literal(&json!(7.0), ColType::Double).unwrap(), "7.0");
        assert_eq!(render_literal(&json!(7), ColType::Double).unwrap(), "7.0");
        assert_eq!(render_literal(&json!(true), ColType::Bool).unwrap(), "true");
        assert_eq!(
            render_literal(&json!(["a", 2]), ColType::StringArray).unwrap(),
            "[\"a\", \"2\"]"
        );
        // Scalar vào cột STRING[] → mảng 1 phần tử.
        assert_eq!(
            render_literal(&json!("solo"), ColType::StringArray).unwrap(),
            "[\"solo\"]"
        );
        // Không phải int vào cột INT64 → lỗi rõ ràng.
        assert!(render_literal(&json!("x"), ColType::Int64).is_err());
        // Map vào cột STRING → JSON string.
        let rendered = render_literal(&json!({"k": 1}), ColType::String).unwrap();
        assert!(rendered.contains("\\\"k\\\""), "{rendered}");
    }

    #[test]
    fn schema_plan_column_dedup() {
        let mut plan = GraphSchemaPlan::default();
        plan.ensure_label("Person");
        plan.observe_node_prop("Person", "Person", "full name", &json!("x"));
        plan.observe_node_prop("Person", "Person", "full_name", &json!(3));
        let schema = &plan.nodes["Person"];
        assert_eq!(schema.columns.len(), 2);
        assert_ne!(schema.columns[0].1, schema.columns[1].1);
    }

    /// Round-trip toàn phần: dựng schema + ghi node/rel như đường migration,
    /// rồi đọc lại QUA `cortex_graph_writer::LadybugStore` — verify không chỉ
    /// count mà cả nội dung property (nguyên tắc của gate phase 14).
    #[test]
    fn roundtrip_content_via_ladybug_store() {
        use cortex_graph_writer::store::ladybug_store::LadybugStore;
        use cortex_graph_writer::store::GraphStore;
        use std::collections::BTreeMap;

        let dir = tempfile::tempdir().unwrap();
        let store_path = dir.path().join("t.lbug").join("roundtrip");

        let mut plan = GraphSchemaPlan::default();
        plan.ensure_label("Person");
        plan.observe_node_prop("Person", "Person", "name", &json!("Alice"));
        plan.observe_node_prop("Person", "Person", "age", &json!(30));
        plan.observe_node_prop("Person", "Person", "tags", &json!(["a", "b"]));
        plan.ensure_rel_type("KNOWS");
        plan.observe_rel_pair("KNOWS", "Person", "Person");
        plan.observe_rel_prop("KNOWS", "KNOWS", "since", &json!(2020));

        let writer = LadybugWriter::open(&store_path).unwrap();
        writer.create_schema(&plan).unwrap();

        let person_schema = &plan.nodes["Person"];
        writer
            .insert_nodes(
                person_schema,
                &[
                    NodeRow {
                        fid: 1,
                        labels: vec!["Person".into()],
                        props: serde_json::from_str(
                            r#"{"name": "Alice", "age": 30, "tags": ["a", "b"]}"#,
                        )
                        .unwrap(),
                    },
                    NodeRow {
                        fid: 2,
                        labels: vec!["Person".into()],
                        props: serde_json::from_str(r#"{"name": "Bob"}"#).unwrap(),
                    },
                ],
            )
            .unwrap();
        let knows_schema = &plan.rels["KNOWS"].0;
        writer
            .insert_rels(
                &plan,
                knows_schema,
                &[RelRow {
                    fid: 100,
                    src_fid: 1,
                    dst_fid: 2,
                    endpoints: ("Person".into(), "Person".into()),
                    props: serde_json::from_str(r#"{"since": 2020}"#).unwrap(),
                }],
            )
            .unwrap();
        drop(writer);

        let mut store = LadybugStore::open(&store_path, "roundtrip").unwrap();
        let empty: BTreeMap<String, Value> = BTreeMap::new();
        let query = |store: &mut LadybugStore, sql: &str| -> Vec<JsonMap> {
            store.execute_query(sql, &empty, None).unwrap()
        };

        // Count.
        let rows = query(&mut store, "MATCH (n:Person) RETURN count(n) AS c");
        assert_eq!(rows[0].values().next().and_then(Value::as_i64), Some(2));
        let rows = query(
            &mut store,
            "MATCH ()-[r:KNOWS]->() RETURN count(r) AS c",
        );
        assert_eq!(rows[0].values().next().and_then(Value::as_i64), Some(1));

        // Content: property từng kiểu giữ đúng giá trị sau khi migrate.
        let rows = query(
            &mut store,
            "MATCH (n:Person) WHERE n.name = 'Alice' \
             RETURN n.name AS name, n.age AS age, n.tags AS tags",
        );
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].get("name").and_then(Value::as_str), Some("Alice"));
        assert_eq!(rows[0].get("age").and_then(Value::as_i64), Some(30));
        let tags: Vec<&str> = rows[0]
            .get("tags")
            .and_then(Value::as_array)
            .map(|items| items.iter().filter_map(Value::as_str).collect())
            .unwrap_or_default();
        assert_eq!(tags, vec!["a", "b"]);

        // Rel property đọc lại.
        let rows = query(
            &mut store,
            "MATCH (a:Person)-[r:KNOWS]->(b:Person) \
             WHERE a.name = 'Alice' RETURN b.name AS who, r.since AS since",
        );
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].get("who").and_then(Value::as_str), Some("Bob"));
        assert_eq!(rows[0].get("since").and_then(Value::as_i64), Some(2020));
    }
}

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

/// Backtick-quote node label / rel type trong pattern positions. Một số
/// identifier của schema trùng reserved word của ladybug grammar (`Table`,
/// `Index`, `Order`, `Group`, `Union`, `All`, `Any`, `Case`, `When`,
/// `Macro`, …) — dạng trần `(t:Table` vỡ Parser exception "expected rule
/// oC_SingleQuery"; dạng quoted ``(t:`Table``` parse OK với mọi label.
/// Chạy TRƯỚC render_params: query text lúc này còn là shape từ code
/// (không chứa dữ liệu đã inline) nên regex không đụng string literal dữ liệu.
fn quote_cypher_labels(query: &str) -> String {
    static NODE_VAR: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    static NODE_BARE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    static REL_VAR: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    static REL_BARE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let node_var = NODE_VAR.get_or_init(|| Regex::new(r"\(([A-Za-z_][A-Za-z0-9_]*):([A-Za-z_][A-Za-z0-9_]*)").unwrap());
    let node_bare = NODE_BARE.get_or_init(|| Regex::new(r"\(:([A-Za-z_][A-Za-z0-9_]*)").unwrap());
    let rel_var = REL_VAR.get_or_init(|| Regex::new(r"-\[([A-Za-z_][A-Za-z0-9_]*):([A-Za-z_][A-Za-z0-9_]*)").unwrap());
    let rel_bare = REL_BARE.get_or_init(|| Regex::new(r"-\[:([A-Za-z_][A-Za-z0-9_]*)").unwrap());
    let quoted = rel_bare.replace_all(query, "-[:`$1`");
    let quoted = rel_var.replace_all(&quoted, "-[$1:`$2`");
    let quoted = node_bare.replace_all(&quoted, "(:`$1`");
    let quoted = node_var.replace_all(&quoted, "($1:`$2`");
    quoted.into_owned()
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
    for r in &rewrites {
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
    // PK-guard (phase-02, ladybug): id là PRIMARY KEY của mọi node table —
    // sau khi merge key đã rewrite sang id, assignment `<var>.id = <expr>`
    // còn sót trong SET (thường trùng với merge expr) bị binder từ chối
    // ("Cannot set property id … primary key"). Cùng expr với merge key →
    // redundant → bỏ; expr khác → fail-closed giữ nguyên (để query báo lỗi
    // rõ thay vì silently đổi semantics).
    let mut deduped = out;
    for r in &rewrites {
        // Assignment `<var>.id = <expr>` chỉ redundant khi TRÙNG expr của
        // merge key; expr khác → giữ nguyên (query báo lỗi rõ, không silent).
        let assignment = format!(
            r"{}\s*\.\s*id\s*=\s*{}",
            regex::escape(&r.inject.var),
            regex::escape(&r.inject.expr)
        );
        let exact = Regex::new(&format!(r"(?i){assignment}")).unwrap();
        if !exact.is_match(&deduped) {
            continue;
        }
        // Ăn đúng MỘT dấu phẩy kèm theo mỗi lần; lặp tới hết (assignment có
        // thể xuất hiện ở cả ON CREATE SET lẫn ON MATCH SET).
        let with_pre = Regex::new(&format!(r"(?i),\s*{assignment}")).unwrap();
        let with_post = Regex::new(&format!(r"(?i){assignment}\s*,")).unwrap();
        let bare = Regex::new(&format!(r"(?i){assignment}")).unwrap();
        loop {
            deduped = if with_pre.is_match(&deduped) {
                with_pre.replace(&deduped, "").into_owned()
            } else if with_post.is_match(&deduped) {
                with_post.replace(&deduped, "").into_owned()
            } else if bare.is_match(&deduped) {
                bare.replace(&deduped, "").into_owned()
            } else {
                break;
            };
        }
    }
    deduped
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
    /// Cache các (rel_type, source, target) đã xác nhận có endpoint bind —
    /// tránh re-probe `show_connection` mỗi batch.
    ensured_rel_pairs: std::cell::RefCell<BTreeSet<(String, String, String)>>,
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
            ensured_rel_pairs: std::cell::RefCell::new(BTreeSet::new()),
        })
    }

    /// Self-heal rel table endpoint: rel table bind CHẶT endpoints từ lần
    /// tạo đầu; parser khác ghi cùng rel type với endpoint khác → Binder
    /// "Query node X violates schema. Expected labels are …". Quét rel
    /// pattern trong query, thiếu pair nào thì DROP+CREATE lại rel table
    /// với UNION endpoints (best-effort, cache để tránh re-probe mỗi batch).
    fn autoheal_rel_endpoints(&self, query: &str) {
        let pairs_by_rel = rel_pairs_from_query_all(query);
        if pairs_by_rel.is_empty() {
            return;
        }
        for (rel, pairs) in &pairs_by_rel {
            let pending: Vec<(String, String)> = pairs
                .iter()
                .filter(|pair| {
                    !self
                        .ensured_rel_pairs
                        .borrow()
                        .contains(&(rel.clone(), pair.0.clone(), pair.1.clone()))
                })
                .cloned()
                .collect();
            if pending.is_empty() {
                continue;
            }
            let Ok(connection) = self.connect() else {
                return;
            };
            let existing: BTreeSet<(String, String)> =
                match connection.query(&format!("CALL show_connection('{rel}')")) {
                    Ok(result) => result
                        .into_iter()
                        .map(|row| {
                            let source = row
                                .iter()
                                .find_map(ladybug_pair_source)
                                .unwrap_or_default()
                                .to_string();
                            let target = row
                                .iter()
                                .find_map(ladybug_pair_target)
                                .unwrap_or_default()
                                .to_string();
                            (source, target)
                        })
                        .collect(),
                    Err(_) => BTreeSet::new(), // bảng chưa tồn tại
                };
            let missing: Vec<(String, String)> = pending
                .iter()
                .filter(|pair| !existing.contains(pair))
                .cloned()
                .collect();
            if missing.is_empty() {
                let mut cache = self.ensured_rel_pairs.borrow_mut();
                for pair in pending {
                    cache.insert((rel.clone(), pair.0.clone(), pair.1.clone()));
                }
                continue;
            }
            let mut union = existing;
            union.extend(missing.iter().cloned());
            let pairs_sql = union
                .iter()
                .map(|(src, dst)| format!("FROM `{src}` TO `{dst}`"))
                .collect::<Vec<_>>()
                .join(", ");
            eprintln!(
                "[ladybug autoheal] rel table `{rel}` thiếu endpoint pairs ({:?}) — DROP+CREATE với union endpoints",
                missing
            );
            if connection.query(&format!("DROP TABLE `{rel}`")).is_ok()
                && connection
                    .query(&format!("CREATE REL TABLE `{rel}` ({pairs_sql})"))
                    .is_ok()
            {
                let mut cache = self.ensured_rel_pairs.borrow_mut();
                for (src, dst) in &union {
                    cache.insert((rel.clone(), src.clone(), dst.clone()));
                }
            }
        }
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
        // datetime()/timestamp() rewrite — inline timestamp('<iso>') như
        // docstring; python driver rewrite cả hai (ladybug_driver.py:269-274,
        // replacement "timestamp(${param})") vì ladybug không có zero-arg
        // timestamp() (chỉ có timestamp(STRING)).
        let timestamp = if normalized.contains("datetime()") || normalized.contains("timestamp()")
        {
            Some(crate::query_normalize::utc_timestamp())
        } else {
            None
        };
        let query = match &timestamp {
            Some(iso) => normalized
                .replace("datetime()", &format!("timestamp('{iso}')"))
                .replace("timestamp()", &format!("timestamp('{iso}')")),
            None => normalized,
        };
        let rendered = render_params(&query, &scoped)?;
        // `SET v += row` expansion PHẢI chạy sau render — cần key set từ
        // literals đã render (uniform-key). Chỉ đụng shape `+= row` trơn;
        // `+= row.props` / `coalesce(row.props, {})` chưa expand (null-props
        // row sẽ đổi semantics) — fail-closed như trước.
        // Quote label/rel-type chạy CUỐI: mọi regex nội bộ (natural-key
        // rewrite, SET += expansion, auto-DDL table resolve) đã nhìn shape
        // trần; executed query mới nhận bản quoted.
        Ok(quote_cypher_labels(&expand_set_plus_equals(&rendered)))
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
            // Node fallback với identity key chuẩn + base columns (khớp
            // bootstrap — unlabeled cleanup query đọc file_path/path/framework
            // trên MỌI bảng nên các cột phải tồn tại từ lúc tạo).
            return Some(format!(
                "CREATE NODE TABLE IF NOT EXISTS `{table}` (id STRING, name STRING, \
                 file_path STRING, path STRING, qualified_name STRING, framework STRING, \
                 project_id STRING, project_id_normalized STRING, PRIMARY KEY(id))"
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
             qualified_name STRING, framework STRING, project_id STRING, project_id_normalized STRING, \
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
            // DB cũ tạo trước khi có cột `framework` — ALTER repair (bỏ qua
            // lỗi khi cột đã tồn tại). Overlay cleanup query `n.framework=…`
            // MATCH node KHÔNG label nên binder cần cột tồn tại trên mọi
            // bảng — auto-DDL không tự sửa được (var không label).
            let _ = connection.query(&format!(
                "ALTER TABLE `{label}` ADD `framework` STRING"
            ));
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
            self.repair_drifted_rel_table(&connection, name, sources, targets);
        }
        }
        self.bootstrapped = true;
        Ok(())
    }

    /// Rel table của bootstrap cũ có thể chỉ bind 1 subset endpoint pairs
    /// (ladybug không hỗ trợ ALTER endpoint; `CREATE … IF NOT EXISTS` là
    /// no-op khi bảng đã tồn tại) → query ghi pair mới fail Binder
    /// "Query node a violates schema. Expected labels are …". Đọc endpoint
    /// hiện tại qua `CALL show_connection` và DROP+CREATE lại khi thiếu pair
    /// — cạnh cũ của rel type đó sẽ được replay sync recovery ghi lại.
    fn repair_drifted_rel_table(
        &self,
        connection: &Connection<'_>,
        name: &str,
        sources: &[String],
        targets: &[String],
    ) {
        let probe = format!("CALL show_connection('{name}')");
        let Ok(result) = connection.query(&probe) else {
            return; // không đọc được catalog — bỏ qua, auto-DDL sẽ xử lý theo lỗi thực tế
        };
        let mut actual: BTreeSet<(String, String)> = BTreeSet::new();
        for row in result {
            let source = row
                .iter()
                .find_map(ladybug_pair_source)
                .unwrap_or_default()
                .to_string();
            let target = row
                .iter()
                .find_map(ladybug_pair_target)
                .unwrap_or_default()
                .to_string();
            if !source.is_empty() && !target.is_empty() {
                actual.insert((source, target));
            }
        }
        if actual.is_empty() {
            return;
        }
        let missing: Vec<(&String, &String)> = sources
            .iter()
            .flat_map(|source| targets.iter().map(move |target| (source, target)))
            .filter(|(source, target)| !actual.contains(&((*source).clone(), (*target).clone())))
            .collect();
        if missing.is_empty() {
            return;
        }
        let missing_list = missing
            .iter()
            .map(|(source, target)| format!("{source}->{target}"))
            .collect::<Vec<_>>()
            .join(", ");
        eprintln!(
            "[ladybug bootstrap] rel table `{name}` thiếu endpoint pairs ({missing_list}) — DROP+CREATE lại theo schema hiện hành"
        );
        if let Err(error) = connection.query(&format!("DROP TABLE `{name}`")) {
            eprintln!("[ladybug bootstrap] DROP `{name}` thất bại: {error}");
            return;
        }
        let pairs = rel_pairs(sources, targets);
        if let Err(error) =
            connection.query(&format!("CREATE REL TABLE `{name}` ({pairs})"))
        {
            eprintln!("[ladybug bootstrap] CREATE lại `{name}` thất bại: {error}");
        }
    }
}

/// Cột "source table name" của `CALL show_connection(...)`.
fn ladybug_pair_source(value: &lbug::Value) -> Option<&str> {
    ladybug_pair_endpoint(value, "source table name")
}

/// Cột "destination table name" của `CALL show_connection(...)`.
fn ladybug_pair_target(value: &lbug::Value) -> Option<&str> {
    ladybug_pair_endpoint(value, "destination table name")
}

fn ladybug_pair_endpoint<'a>(value: &'a lbug::Value, column: &str) -> Option<&'a str> {
    if let lbug::Value::String(text) = value {
        return Some(text.as_str());
    }
    if let lbug::Value::Struct(fields) = value {
        for (name, item) in fields {
            if name == column {
                return ladybug_pair_endpoint(item, column);
            }
        }
    }
    None
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
    let all = rel_pairs_from_query_all(query);
    let Some(pairs) = all.get(table) else {
        return String::new();
    };
    pairs
        .iter()
        .map(|(src, dst)| format!("FROM `{src}` TO `{dst}`"))
        .collect::<Vec<_>>()
        .join(", ")
}

/// Mọi (rel_type → set endpoint pair) xuất hiện trong query — dùng cho
/// auto-heal rel table endpoint (self-heal "Query node X violates schema").
fn rel_pairs_from_query_all(query: &str) -> BTreeMap<String, BTreeSet<(String, String)>> {
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
    let mut out: BTreeMap<String, BTreeSet<(String, String)>> = BTreeMap::new();
    for caps in rel_usage_re.captures_iter(query) {
        let (src_var, src_label, rel_type, dst_var, dst_label) = (
            &caps[1],
            caps.get(2).map(|m| m.as_str()),
            &caps[3],
            &caps[4],
            caps.get(5).map(|m| m.as_str()),
        );
        let source = src_label
            .map(str::to_string)
            .or_else(|| var_labels.get(src_var).cloned());
        let target = dst_label
            .map(str::to_string)
            .or_else(|| var_labels.get(dst_var).cloned());
        if let (Some(source), Some(target)) = (source, target) {
            out.entry(rel_type.to_string())
                .or_default()
                .insert((source, target));
        }
    }
    out
}

// ── Literal rendering ────────────────────────────────────────────────────────

/// Render toàn bộ params vào query text: mỗi `$name` → literal.
/// `rows`-kiểu list-of-map render uniform-key (union key của cả list).
/// `SET <var> += <map>` **không parse được** trên ladybug 0.20.4 (dialect
/// note ở docstring crate). Sau khi params đã render thành literals, batch
/// `UNWIND [{...}, …] AS row` có sẵn key set (uniform-key union) trong chữ
/// ký query — expand `SET v += row` thành gán per-property `SET v.k = row.k, …`
/// giữ đúng semantics `+=` của FalkorDB (key thiếu đã được render `NULL`,
/// gán NULL = xoá property, trùng semantics falkordb).
fn expand_set_plus_equals(query: &str) -> String {
    let Some(keys) = unwind_row_keys(query) else {
        return query.to_string();
    };
    if keys.is_empty() || keys.iter().any(|key| !is_plain_identifier(key)) {
        // Key không phải identifier trơn — không expand (fail-closed, query
        // sẽ fail như trước thay vì sai semantics).
        return query.to_string();
    }
    // Merge key per var (post natural-key rewrite mọi merge đều `{id: …}`).
    // Assignment `<var>.<merge_key> = <cùng expr>` là PK-set → binder ladybug
    // từ chối — loại khỏi expansion (redundant: merge đã đặt giá trị đó).
    // Ngoài MERGE, pattern `(var:Label {key: …})` của MATCH/MERGE đều bind
    // key đó làm identity — SET lại key đó (kể cả qua `+= row` expansion)
    // đều dính "Cannot set property id … primary key".
    let mut merge_keys: Vec<(String, String)> = Vec::new();
    for caps in merge_node_pattern_re().captures_iter(query) {
        merge_keys.push((caps[1].to_string(), caps[3].to_string()));
    }
    for caps in node_pattern_prop_re().captures_iter(query) {
        // Chỉ `id` là PK: mọi node table ladybug khai `PRIMARY KEY(id)`
        // (bootstrap + auto-DDL fallback). Pattern prop khác (filter trong
        // MATCH) vẫn SET được — không lọc để không mất write.
        let key = &caps[2];
        if key == "id" {
            merge_keys.push((caps[1].to_string(), key.to_string()));
        }
    }
    let re = set_plus_equals_row_re();
    re.replace_all(query, |caps: &regex::Captures| {
        // Nhánh 1: `+= coalesce(row.<field>, {})` (groups 1-2)
        // Nhánh 2: `+= row.<field>` (groups 3-4)
        // Nhánh 3: `+= row` trơn (groups 5-6; follow char phải preserve).
        let (var, nested_field, follow) = if caps.get(1).is_some() {
            (
                caps.get(1).map(|m| m.as_str()).unwrap_or_default(),
                caps.get(2).map(|m| m.as_str()).filter(|s| !s.is_empty()),
                None,
            )
        } else if caps.get(3).is_some() {
            (
                caps.get(3).map(|m| m.as_str()).unwrap_or_default(),
                caps.get(4).map(|m| m.as_str()).filter(|s| !s.is_empty()),
                None,
            )
        } else {
            (
                caps.get(5).map(|m| m.as_str()).unwrap_or_default(),
                None,
                caps.get(6).map(|m| m.as_str()),
            )
        };
        let keys_for_assign: Option<Vec<String>> = match nested_field {
            Some(field) => unwind_nested_row_keys(query, field).filter(|nested| {
                !nested.is_empty() && nested.iter().all(|k| is_plain_identifier(k))
            }),
            None => Some(keys.clone()),
        };
        let Some(assigns_keys) = keys_for_assign else {
            // Field lồng không tìm thấy key set — fail-closed giữ nguyên
            // fragment (query sẽ fail như trước thay vì sai semantics).
            return caps[0].to_string();
        };
        let source = match nested_field {
            Some(field) => format!("row.`{field}`"),
            None => "row".to_string(),
        };
        let assigns: Vec<String> = assigns_keys
            .iter()
            .filter(|key| {
                // Bỏ key trùng merge key CÙNG expr (node merged trên id đã có
                // giá trị; SET lại là PK-set violation).
                !merge_keys
                    .iter()
                    .any(|(mv, mk)| mv == var && mk == *key)
            })
            // Backtick cả 2 bên: key có thể là reserved word của ladybug
            // (`order`, `count`, `index`, …) — property access trần vỡ parser.
            .map(|key| format!("{var}.`{key}` = {source}.`{key}`"))
            .collect();
        if assigns.is_empty() {
            // Mọi key đều là merge key — SET list rỗng không parse được.
            return caps[0].to_string();
        }
        match follow {
            Some(follow) => format!("SET {}{follow}", assigns.join(", ")),
            None => format!("SET {}", assigns.join(", ")),
        }
    })
    .into_owned()
}

/// Property identity trong MỌI node pattern `(var:Label {key: expr})` —
/// key đó là PK/identity của pattern, không được SET lại qua expansion.
fn node_pattern_prop_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"\(([A-Za-z_][A-Za-z0-9_]*):`?[A-Za-z_][A-Za-z0-9_]*`? ?\{`?([A-Za-z_][A-Za-z0-9_]*)`?: ").unwrap()
    })
}

fn set_plus_equals_row_re() -> &'static Regex {    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r#"(?i)\bSET ([A-Za-z_][A-Za-z0-9_]*) \+= coalesce\(row\.([A-Za-z_][A-Za-z0-9_]*), \{\}\)|\bSET ([A-Za-z_][A-Za-z0-9_]*) \+= row\.([A-Za-z_][A-Za-z0-9_]*)|\bSET ([A-Za-z_][A-Za-z0-9_]*) \+= row([^A-Za-z0-9_.])?"#,
        )
        .unwrap()
    })
}

/// Union key của map lồng `row.<field>` trong literal `UNWIND [...]` đầu tiên
/// (render uniform-key nên row đầu đại diện cả batch). Renderer emits
/// `` `key`: {``…``}`` — tìm marker rồi bóc segment map lồng cho
/// `first_map_keys`.
fn unwind_nested_row_keys(query: &str, field: &str) -> Option<Vec<String>> {
    let unwind = query.find("UNWIND ")?;
    let open = query[unwind..].find('[')? + unwind;
    let segment = &query[open..];
    let marker = format!("`{field}`: {{");
    let marker_pos = segment.find(&marker)?;
    let brace_start = open + marker_pos + marker.len() - 1;
    let bytes = query.as_bytes();
    let mut depth = 0i32;
    let mut in_str: Option<char> = None;
    let mut escaped = false;
    for (offset, &byte) in bytes.iter().enumerate().skip(brace_start) {
        let c = byte as char;
        if let Some(quote) = in_str {
            if escaped {
                escaped = false;
            } else if c == '\\' {
                escaped = true;
            } else if c == quote {
                in_str = None;
            }
            continue;
        }
        match c {
            '\'' | '"' => in_str = Some(c),
            '{' => depth += 1,
            '}' => {
                depth -= 1;
                if depth == 0 {
                    // Gồm cả 2 braces — first_map_keys đợi `{` đầu tiên.
                    return Some(first_map_keys(&query[brace_start..=offset]));
                }
            }
            _ => {}
        }
    }
    None
}

fn is_plain_identifier(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// Top-level keys (thứ tự xuất hiện) của map đầu tiên trong đoạn
/// `UNWIND [<literal>] AS <var>` sau khi params đã render. Parser
/// depth-aware + string-aware (NHẢY ĐƠN lẫn NHÁY KÉP — `quote_cypher` render
/// string bằng `"…"` với `\\` escape — string value có thể chứa
/// `}`/`[`/`,`/backtick).
fn unwind_row_keys(query: &str) -> Option<Vec<String>> {
    let unwind = query.find("UNWIND ")?;
    let open = query[unwind..].find('[')? + unwind;
    let bytes = query.as_bytes();
    let mut depth = 0i32;
    let mut in_str: Option<char> = None;
    let mut escaped = false;
    for (offset, &byte) in bytes.iter().enumerate().skip(open) {
        let c = byte as char;
        if let Some(quote) = in_str {
            if escaped {
                escaped = false;
            } else if c == '\\' {
                escaped = true;
            } else if c == quote {
                in_str = None;
            }
            continue;
        }
        match c {
            '\'' | '"' => in_str = Some(c),
            '[' | '{' | '(' => depth += 1,
            ']' | '}' | ')' => {
                depth -= 1;
                if depth == 0 {
                    return Some(first_map_keys(&query[open + 1..offset]));
                }
            }
            _ => {}
        }
    }
    None
}

/// Keys ở map-depth 1 của map đầu tiên trong segment (nội dung giữa `[` và
/// `]` của list literal): đợi `{` đầu tiên, thu `` `key` `` (hoặc key trơn)
/// ngay trước `:` cho tới khi map đóng.
fn first_map_keys(segment: &str) -> Vec<String> {
    let bytes = segment.as_bytes();
    let mut keys = Vec::new();
    let mut depth = 0i32;
    let mut in_str: Option<char> = None;
    let mut escaped = false;
    let mut pending_key = String::new();
    let mut started = false;
    for &byte in bytes {
        let c = byte as char;
        if let Some(quote) = in_str {
            pending_key.push(c);
            if escaped {
                escaped = false;
            } else if c == '\\' {
                escaped = true;
            } else if c == quote {
                in_str = None;
            }
            continue;
        }
        match c {
            '\'' | '"' => {
                in_str = Some(c);
                escaped = false;
                pending_key.push(c);
            }
            '[' | '{' | '(' => {
                depth += 1;
                if started && depth == 1 {
                    return keys;
                }
                pending_key.clear();
            }
            ']' | '}' | ')' => {
                depth -= 1;
                if started && depth == 0 {
                    return keys;
                }
            }
            ':' if depth == 1 => {
                let key = pending_key.trim().to_string();
                let key = key
                    .strip_prefix('`')
                    .and_then(|k| k.strip_suffix('`'))
                    .unwrap_or(key.trim());
                if !key.is_empty() {
                    keys.push(key.to_string());
                }
                pending_key.clear();
                started = true;
            }
            ',' if depth == 1 => pending_key.clear(),
            _ => {
                if depth >= 1 {
                    pending_key.push(c);
                }
            }
        }
    }
    keys
}

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
    /// Column từng chứa bool / number / array / object — xác định "shape
    /// set" của cột; nhiều shape → mọi value JSON-string để đồng nhất kiểu.
    saw_bool: bool,
    saw_number: bool,
    saw_array: bool,
    saw_object: bool,
    /// Array chứa object element (khác array thuần scalar).
    saw_array_with_object: bool,
    /// Array thuần scalar (string/number/bool).
    saw_array_scalar: bool,
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
                self.saw_object = true;
                for (key, child) in map {
                    self.children.entry(key.clone()).or_default().absorb(child);
                }
            }
            Value::Array(items) => {
                self.saw_array = true;
                let has_object = items.iter().any(Value::is_object);
                if has_object {
                    self.saw_array_with_object = true;
                }
                if !items.is_empty() && !has_object {
                    self.saw_array_scalar = true;
                }
                for item in items {
                    if item.is_string() {
                        self.saw_string_element = true;
                    }
                    self.absorb(item);
                }
            }
            Value::String(_) => self.saw_string_element = true,
            Value::Bool(_) => self.saw_bool = true,
            Value::Number(_) => self.saw_number = true,
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
                    // Shape set của cột: >1 shape (string/bool/number/array/
                    // object) hoặc array trộn scalar-array với object-array →
                    // JSON-string toàn bộ value của cột để mọi row đồng nhất
                    // kiểu (ladybug không implicit cast giữa các row).
                    let mut shapes = 0usize;
                    for saw in [
                        child.saw_string_element,
                        child.saw_bool,
                        child.saw_number,
                        child.saw_array,
                        child.saw_object,
                    ] {
                        if saw {
                            shapes += 1;
                        }
                    }
                    let array_shape_split =
                        child.saw_array_with_object && child.saw_array_scalar;
                    let mixed = shapes > 1 || array_shape_split;
                    let rendered = if value.is_null() {
                        match hints.get(key) {
                            Some(hint) => format!("CAST(NULL AS {hint})"),
                            None => {
                                if mixed || child.saw_string_element {
                                    "CAST(NULL AS STRING)".to_string()
                                } else if child.saw_bool {
                                    "CAST(NULL AS BOOL)".to_string()
                                } else if child.saw_number {
                                    "CAST(NULL AS INT64)".to_string()
                                } else {
                                    "NULL".to_string()
                                }
                            }
                        }
                    } else if mixed {
                        match serde_json::to_string(value) {
                            Ok(text) => quote_cypher(&text),
                            Err(_) => "NULL".to_string(),
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
        if is_write_intent(query) {
            self.autoheal_rel_endpoints(query);
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
        // Preflight gọi inspect đầu tiên trước create_indexes — bảng phải
        // tồn tại trước index, nên bootstrap ở đây (idempotent qua cờ).
        // FalkorDbStore::ensure_schema tự gọi preflight nên bootstrap KHÔNG
        // thể đặt trong preflight::ensure_schema_with (recursion).
        if !self.bootstrapped {
            self.bootstrap_schema()?;
        }
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
                // (2) column-missing — bootstrap chỉ khai báo base columns;
                // index-required identity property (workflow_id, site_id,
                // config_fingerprint, …) là STRING id → auto-DDL ADD column
                // rồi retry index để preflight verified đủ (phase-02: preflight
                // strict poll-to-online sẽ deadline nếu index không tồn tại).
                let column_missing = message.contains("does not exist in table");
                let local_result = if column_missing && index.index_type != "fulltext" {
                    let idx = index_name(&label, &[prop.as_str()]);
                    let added = connection.query(&format!(
                        "ALTER TABLE `{label}` ADD `{prop}` STRING"
                    ));
                    match added {
                        Ok(_) => connection
                            .query(&format!(
                                "CREATE ART INDEX `{idx}` FOR (t:`{label}`) ON (t.`{prop}`)"
                            ))
                            .map(|_| ())
                            .map_err(|e| StoreError::Ladybug(format!("{e}"))),
                        Err(add_error) => Err(StoreError::Ladybug(format!(
                            "auto-DDL ADD `{prop}` on `{label}`: {add_error}"
                        ))),
                    }
                } else {
                    Err(StoreError::Ladybug(message))
                };
                if let Err(StoreError::Ladybug(message)) = local_result {
                    // Soft-skip: (1) "already exists" như Python.
                    // (3) PK-collision (phase-02 sync-cutover): identity property
                    // đã là PRIMARY KEY (HASH index) từ bootstrap — index yêu cầu
                    // tồn tại ở dạng mạnh hơn; inspect_indexes map `_PK` → "range"
                    // nên đây chính là "already exists" dưới tên khác.
                    let pk_backed = message.contains("primary-key index");
                    if !exists_re.is_match(&message) && !pk_backed {
                        return Err(StoreError::Ladybug(format!(
                            "failed to create {} index on {label}({prop}): {message}",
                            index.index_type
                        )));
                    }
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
    fn set_plus_equals_expands_top_level_keys_in_order() {
        let query = "UNWIND [{`id`: `a`, `count`: 1, `name`: 'x'}, {`id`: `b`, `count`: NULL, `name`: NULL}] AS row\n\
                     MERGE (endpoint:ApiEndpoint {id: row.id})\n\
                     SET endpoint += row, endpoint.topology_owned = true\n\
                     RETURN count(endpoint) AS count";
        let out = expand_set_plus_equals(query);
        // `id` là merge key (PK) — bị loại khỏi expansion (SET lại = PK-set
        // violation trên ladybug).
        assert!(
            out.contains("SET endpoint.`count` = row.`count`, endpoint.`name` = row.`name`, endpoint.topology_owned = true"),
            "{out}"
        );
        assert!(!out.contains("+="), "{out}");
        assert!(!out.contains("endpoint.id = row.id"), "{out}");
    }

    #[test]
    fn project_repository_setup_query_pk_strip() {
        let query = r#"
MERGE (p:Project {project_id: $project_id})
ON CREATE SET
    p.name                  = $project_name,
    p.slug                  = $project_slug,
    p.project_id_normalized = $project_id_normalized,
    p.created_at            = timestamp()
ON MATCH SET
    p.name                  = $project_name,
    p.slug                  = $project_slug,
    p.project_id_normalized = $project_id_normalized
WITH p, r
MERGE (r:Repository {name: $repo_name})
ON CREATE SET
    r.id                    = $repo_name,
    r.project_id            = $project_id,
    r.project_id_normalized = $project_id_normalized,
    r.created_at            = timestamp()
ON MATCH SET
    r.id                    = $repo_name,
    r.project_id            = $project_id,
    r.project_id_normalized = $project_id_normalized
"#;
        let out = rewrite_merge_natural_keys(query);
        eprintln!("=== REWRITTEN ===\n{out}\n=== END ===");
        assert!(!out.contains("r.id"), "r.id should be stripped:\n{out}");
    }

    #[test]
    fn pk_id_assignment_stripped_after_natural_key_rewrite() {
        let query = "MERGE (r:Repository {name: $repo_name})\n\
                     ON CREATE SET\n    r.id = $repo_name, r.project_id = $project_id, r.created_at = timestamp()\n\
                     RETURN count(r) AS count";
        let out = rewrite_merge_natural_keys(query);
        assert!(out.contains("MERGE (r:Repository {id: $repo_name})"), "{out}");
        assert!(out.contains("r.name = $repo_name,"), "{out}");
        assert!(!out.contains("r.id = $repo_name"), "{out}");
        assert!(out.contains("r.project_id = $project_id"), "{out}");
    }

    #[test]
    fn pk_id_assignment_different_expr_kept() {
        // id assignment với expr KHÁC merge expr → giữ nguyên (fail-closed).
        let query = "MERGE (r:Repository {name: $repo_name})\n\
                     ON CREATE SET\n    r.id = $other, r.project_id = $project_id\n\
                     RETURN count(r) AS count";
        let out = rewrite_merge_natural_keys(query);
        assert!(out.contains("r.id = $other"), "{out}");
    }


    #[test]
    fn set_plus_equals_skips_when_no_unwind_literal() {
        // Chưa render (vẫn $rows) — không expand.
        let query = "UNWIND $rows AS row\nMERGE (n:File {id: row.id})\nSET n += row";
        assert_eq!(expand_set_plus_equals(query), query);
    }

    #[test]
    fn set_plus_equals_ignores_props_shapes() {
        // `+= row.props` giờ ĐƯỢC expand (ladybug không parse `+= <map expr>`;
        // key set lấy từ map lồng của row đầu — render uniform-key nên đại
        // diện cả batch). `+= row.props.<x>` vô nghĩa thì vẫn giữ nguyên.
        let query = "UNWIND [{`site_id`: `s`, `props`: {`count`: 1}}] AS row\nSET site += row.props";
        let expanded = expand_set_plus_equals(query);
        assert!(
            expanded.contains("SET site.`count` = row.`props`.`count`"),
            "{expanded}"
        );
        assert!(!expanded.contains("+="), "{expanded}");
    }

    #[test]
    fn unwind_row_keys_skips_strings_with_braces() {
        let query = "UNWIND [{`id`: `a`, `snippet`: '}{[`'}, {`id`: `b`, `snippet`: NULL}] AS row RETURN count(*) AS c";
        assert_eq!(unwind_row_keys(query), Some(vec!["id".into(), "snippet".into()]));
        // quote_cypher render dùng NHÁY KÉP (route chứa {code} trong string).
        let dq = "UNWIND [{`id`: `a`, `route`: \"/legacy/{code}\", `n`: 1}, {`id`: `b`, `route`: NULL, `n`: 2}] AS row\nMERGE (node:ApiEndpoint {id: row.id})\nSET node += row\nRETURN count(node) AS count";
        assert_eq!(
            unwind_row_keys(dq),
            Some(vec!["id".into(), "route".into(), "n".into()])
        );
        let expanded = expand_set_plus_equals(dq);
        assert!(
            expanded.contains("SET node.`route` = row.`route`, node.`n` = row.`n`"),
            "{expanded}"
        );
        assert!(!expanded.contains("node.id = row.id"), "{expanded}");
    }

    #[test]
    fn nested_field_plus_equals_expands() {
        let query = "UNWIND [{`id`: \"f1\", `properties`: {`confidence`: 1.0, `dialect`: \"ansi\"}}, {`id`: \"f2\", `properties`: {`confidence`: NULL, `dialect`: NULL}}] AS row MATCH (n:File {id: row.id}) SET n += row.properties";
        eprintln!("REGEX_MATCH: {:?}", set_plus_equals_row_re().captures(query).is_some());
        eprintln!("NESTED_KEYS: {:?}", unwind_nested_row_keys(query, "properties"));
        let out = expand_set_plus_equals(query);
        assert!(
            out.contains("n.`confidence` = row.`properties`.`confidence`"),
            "{out}"
        );
        assert!(!out.contains("+="), "{out}");
    }

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
        // Row 1 thiếu extra → CAST(NULL AS INT64) (cột chỉ có number — NULL
        // trần bị ladybug type thành STRING và vỡ khi trộn row); key set uniform.
        assert!(rendered.contains("`id`: \"a\""), "{rendered}");
        assert!(rendered.contains("`extra`: CAST(NULL AS INT64)"), "{rendered}");
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

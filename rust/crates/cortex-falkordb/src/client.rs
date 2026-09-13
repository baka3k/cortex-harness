//! Redis-protocol client cho FalkorDB / Redis graph engine.
//!
//! Wire contract (đối chiếu falkordb-py `Graph._query`, bản driver Python đang
//! chạy trong repo):
//! - đọc:  `GRAPH.RO_QUERY <graph> <query> --compact [timeout <ms>]`
//! - ghi:  `GRAPH.QUERY  <graph> <query> --compact [timeout <ms>]`
//! - params: header ``CYPHER `k`=v `` chèn trước query (xem `build_params_header`).
//!
//! Lưu ý: lệnh `GRAPH.ROQUERY` (tên trong phase-01 plan) *không tồn tại* trên
//! server — client thật của driver dùng `GRAPH.RO_QUERY`. Spike đã xác nhận
//! trực tiếp trên server (xem reports/phase01-decision.md).

use std::collections::BTreeMap;

use redis::{Commands, Connection, RedisResult};

use crate::schema::GraphSchema;
use crate::value::{FalkorValue, ParseError, parse_value};

/// Header một cột result set: `[column_type, name]` (compact mode).
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Column {
    pub column_type: i64,
    pub name: String,
}

/// Kết quả một query: header + records + statistics (dạng chuỗi "Key: value").
#[derive(Debug, Clone)]
pub struct QueryResult {
    pub header: Vec<Column>,
    pub records: Vec<Vec<FalkorValue>>,
    pub statistics: Vec<String>,
}

impl QueryResult {
    /// Tra số liệu thống kê kiểu `Nodes created: 1` theo key.
    pub fn statistic(&self, key: &str) -> Option<i64> {
        let prefix = format!("{key}: ");
        self.statistics.iter().find_map(|line| {
            line.strip_prefix(&prefix)
                .and_then(|rest| rest.trim().parse().ok())
        })
    }

    pub fn internal_execution_time_ms(&self) -> Option<f64> {
        self.statistics.iter().find_map(|line| {
            line.split_once("Query internal execution time: ").and_then(|(_, rest)| {
                rest.trim_end_matches(" milliseconds").trim().parse().ok()
            })
        })
    }
}

#[derive(Debug)]
pub enum ClientError {
    Redis(redis::RedisError),
    Parse(ParseError),
    /// Server trả error (Redis error reply) — giữ nguyên message.
    Server(String),
}

impl std::fmt::Display for ClientError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ClientError::Redis(e) => write!(f, "redis: {e}"),
            ClientError::Parse(e) => write!(f, "parse: {e}"),
            ClientError::Server(m) => write!(f, "server: {m}"),
        }
    }
}

impl std::error::Error for ClientError {}

impl From<redis::RedisError> for ClientError {
    fn from(e: redis::RedisError) -> Self {
        ClientError::Redis(e)
    }
}

impl From<ParseError> for ClientError {
    fn from(e: ParseError) -> Self {
        ClientError::Parse(e)
    }
}

/// Giá trị param Cypher — mã hoá y `stringify_param_value` của falkordb-py.
#[derive(Debug, Clone)]
pub enum Param {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    List(Vec<Param>),
}

impl From<&str> for Param {
    fn from(v: &str) -> Self {
        Param::Str(v.to_string())
    }
}

impl From<String> for Param {
    fn from(v: String) -> Self {
        Param::Str(v)
    }
}

impl From<i64> for Param {
    fn from(v: i64) -> Self {
        Param::Int(v)
    }
}

/// Build header params ``CYPHER `k`=v `` — key bọc backtick, value theo quy tắc
/// stringify_param_value: str→quoted, None→null, bool→"True"/"False" (str() của
/// Python), list→[...], còn lại str().
pub fn build_params_header(params: &BTreeMap<String, Param>) -> String {
    if params.is_empty() {
        return String::new();
    }
    let mut header = String::from("CYPHER ");
    for (key, value) in params {
        header.push('`');
        header.push_str(key);
        header.push_str("`=");
        header.push_str(&stringify_param_value(value));
        header.push(' ');
    }
    header
}

fn quote_string(v: &str) -> String {
    if v.is_empty() {
        return "\"\"".to_string();
    }
    let mut out = String::with_capacity(v.len() + 2);
    out.push('"');
    for ch in v.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            other => out.push(other),
        }
    }
    out.push('"');
    out
}

pub fn stringify_param_value(value: &Param) -> String {
    match value {
        Param::Null => "null".to_string(),
        Param::Str(v) => quote_string(v),
        Param::List(items) => {
            let inner: Vec<String> = items.iter().map(stringify_param_value).collect();
            format!("[{}]", inner.join(","))
        }
        // str(True) == "True" trong Python — giữ đúng để param tương thích.
        Param::Bool(true) => "True".to_string(),
        Param::Bool(false) => "False".to_string(),
        Param::Int(v) => v.to_string(),
        Param::Float(v) => format!("{v}"),
    }
}

/// Client single-connection, RESP2 (khớp `protocol=2` của driver Python).
/// Giữ cache bảng tên schema theo graph như falkordb-py giữ trên `Graph`.
pub struct FalkorDbClient {
    connection: Connection,
    schemas: std::collections::HashMap<String, GraphSchema>,
}

impl FalkorDbClient {
    pub fn connect(host: &str, port: u16) -> RedisResult<Self> {
        let client = redis::Client::open((host, port))?;
        let connection = client.get_connection()?;
        Ok(Self {
            connection,
            schemas: std::collections::HashMap::new(),
        })
    }

    /// Connect + đo round-trip PING.
    pub fn connect_verified(host: &str, port: u16) -> Result<Self, ClientError> {
        let mut client = Self::connect(host, port)?;
        let pong: String = client.connection.ping()?;
        if pong != "PONG" {
            return Err(ClientError::Server(format!("PING trả về {pong:?}")));
        }
        Ok(client)
    }

    /// Đọc bảng tên schema hiện hành của graph (DB.LABELS / DB.RELATIONSHIPTYPES
    /// / DB.PROPERTYKEYS — thứ tự trả về chính là id trên dây).
    ///
    /// Các procedure này trả chuỗi trần nên parse không cần bảng tên — dùng
    /// `run_graph_command` trực tiếp với schema rỗng (tránh đệ quy load_schema
    /// ↔ ro_query).
    pub fn load_schema(&mut self, graph: &str) -> Result<GraphSchema, ClientError> {
        let mut scratch = GraphSchema::default();
        let labels = self.call_procedure_strings(graph, "DB.LABELS", "label", &mut scratch)?;
        let relationships = self.call_procedure_strings(
            graph,
            "DB.RELATIONSHIPTYPES",
            "relationshipType",
            &mut scratch,
        )?;
        let properties = self.call_procedure_strings(
            graph,
            "db.propertyKeys",
            "propertyKey",
            &mut scratch,
        )?;
        Ok(GraphSchema::from_tables(labels, relationships, properties))
    }

    fn call_procedure_strings(
        &mut self,
        graph: &str,
        procedure: &str,
        yield_column: &str,
        schema: &mut GraphSchema,
    ) -> Result<Vec<String>, ClientError> {
        let query = format!("CALL {procedure}() YIELD {yield_column} RETURN {yield_column}");
        let result = self.run_graph_command(
            "GRAPH.RO_QUERY",
            graph,
            &query,
            &BTreeMap::new(),
            None,
            schema,
        )?;
        let mut names = Vec::new();
        for record in &result.records {
            if let Some(FalkorValue::String(name)) = record.first() {
                names.push(name.clone());
            }
        }
        Ok(names)
    }

    /// Parse reply compact 3 phần tử [header, records, statistics].
    fn parse_reply(
        raw: &redis::Value,
        schema: &mut GraphSchema,
    ) -> Result<QueryResult, ParseError> {
        let sections = match raw {
            redis::Value::Array(sections) => sections,
            redis::Value::Nil => {
                return Err(ParseError::Malformed("reply rỗng (nil)".into()));
            }
            other => {
                return Err(ParseError::Malformed(format!(
                    "reply phải là array, got {other:?}"
                )));
            }
        };
        if sections.len() < 3 {
            return Err(ParseError::Malformed(format!(
                "reply cần >= 3 section, got {}",
                sections.len()
            )));
        }
        let header_raw = &sections[0];
        let records_raw = &sections[1];
        let stats_raw = &sections[2];

        let mut header = Vec::new();
        for col in match header_raw {
            redis::Value::Array(items) => items,
            other => {
                return Err(ParseError::Malformed(format!("header: {other:?}")));
            }
        } {
            // Compact mode: [column_type, name].
            match col {
                redis::Value::Array(pair) if pair.len() == 2 => {
                    let column_type = match &pair[0] {
                        redis::Value::Int(i) => *i,
                        redis::Value::BulkString(b) => std::str::from_utf8(b)
                            .ok()
                            .and_then(|s| s.parse().ok())
                            .unwrap_or(0),
                        _ => 0,
                    };
                    let name = match &pair[1] {
                        redis::Value::BulkString(b) => String::from_utf8_lossy(b).into_owned(),
                        other => format!("{other:?}"),
                    };
                    header.push(Column { column_type, name });
                }
                // Non-compact fallback: tên cột trần.
                redis::Value::BulkString(b) => header.push(Column {
                    column_type: 1,
                    name: String::from_utf8_lossy(b).into_owned(),
                }),
                other => {
                    return Err(ParseError::Malformed(format!("header col: {other:?}")));
                }
            }
        }

        let mut records = Vec::new();
        let rows = match records_raw {
            redis::Value::Array(rows) => rows,
            other => {
                return Err(ParseError::Malformed(format!("records: {other:?}")));
            }
        };
        for row in rows {
            let cells = match row {
                redis::Value::Array(cells) => cells,
                other => {
                    return Err(ParseError::Malformed(format!("record row: {other:?}")));
                }
            };
            let mut parsed_row = Vec::with_capacity(cells.len());
            for cell in cells {
                parsed_row.push(parse_value(cell, schema)?);
            }
            records.push(parsed_row);
        }

        let statistics = match stats_raw {
            redis::Value::Array(lines) => lines
                .iter()
                .map(|line| match line {
                    redis::Value::BulkString(b) => String::from_utf8_lossy(b).into_owned(),
                    other => format!("{other:?}"),
                })
                .collect(),
            other => {
                return Err(ParseError::Malformed(format!("statistics: {other:?}")));
            }
        };

        Ok(QueryResult {
            header,
            records,
            statistics,
        })
    }

    fn run_graph_command(
        &mut self,
        command: &str,
        graph: &str,
        query: &str,
        params: &BTreeMap<String, Param>,
        timeout_ms: Option<u64>,
        schema: &mut GraphSchema,
    ) -> Result<QueryResult, ClientError> {
        let full_query = format!("{}{}", build_params_header(params), query);
        let mut cmd = redis::cmd(command);
        cmd.arg(graph).arg(&full_query).arg("--compact");
        if let Some(ms) = timeout_ms {
            cmd.arg("timeout").arg(ms);
        }
        let raw: redis::Value = cmd.query(&mut self.connection)?;
        match Self::parse_reply(&raw, schema) {
            Ok(result) => Ok(result),
            // Id mới xuất hiện giữa chừng: refresh bảng tên rồi parse lại đúng 1
            // lần (bắt chước refresh-on-mismatch của falkordb-py).
            Err(ParseError::Schema(_)) => {
                *schema = self.load_schema(graph)?;
                let raw: redis::Value = cmd.query(&mut self.connection)?;
                Ok(Self::parse_reply(&raw, schema)?)
            }
            Err(e) => Err(e.into()),
        }
    }

    fn ro_query_raw(
        &mut self,
        graph: &str,
        query: &str,
        params: &BTreeMap<String, Param>,
        timeout_ms: Option<u64>,
    ) -> Result<QueryResult, ClientError> {
        // Schema cache theo graph — refresh ngay khi parse gặp id lạ
        // (falkordb-py giữ schema trên Graph object và refresh on mismatch).
        if !self.schemas.contains_key(graph) {
            let schema = self.load_schema(graph)?;
            self.schemas.insert(graph.to_string(), schema);
        }
        let mut schema = self.schemas.get(graph).cloned().unwrap_or_default();
        match self.run_graph_command("GRAPH.RO_QUERY", graph, query, params, timeout_ms, &mut schema)
        {
            Ok(result) => {
                self.schemas.insert(graph.to_string(), schema);
                Ok(result)
            }
            // Schema vừa cache stale hơn cả bảng vừa refresh — thử đúng 1 lần
            // nữa với bảng tên tải mới.
            Err(ClientError::Parse(ParseError::Schema(_))) => {
                let fresh = self.load_schema(graph)?;
                let mut fresh = fresh;
                let result =
                    self.run_graph_command("GRAPH.RO_QUERY", graph, query, params, timeout_ms, &mut fresh);
                self.schemas.insert(graph.to_string(), fresh);
                result
            }
            Err(e) => Err(e),
        }
    }

    /// Đọc-only query (`GRAPH.RO_QUERY`). Không đổi graph.
    pub fn ro_query(
        &mut self,
        graph: &str,
        query: &str,
        params: &BTreeMap<String, Param>,
    ) -> Result<QueryResult, ClientError> {
        self.ro_query_raw(graph, query, params, None)
    }

    /// Ghi query (`GRAPH.QUERY`) — MERGE/CREATE/SET/DELETE.
    pub fn query(
        &mut self,
        graph: &str,
        query: &str,
        params: &BTreeMap<String, Param>,
        timeout_ms: Option<u64>,
    ) -> Result<QueryResult, ClientError> {
        // Write có thể tạo label/rel-type/property mới → chủ động refresh cache
        // sau khi ghi thành công (giống lần đầu client khác nhìn thấy schema mới).
        if !self.schemas.contains_key(graph) {
            let schema = self.load_schema(graph)?;
            self.schemas.insert(graph.to_string(), schema);
        }
        let mut schema = self.schemas.get(graph).cloned().unwrap_or_default();
        let result = self.run_graph_command("GRAPH.QUERY", graph, query, params, timeout_ms, &mut schema);
        if result.is_ok() {
            let fresh = self.load_schema(graph)?;
            self.schemas.insert(graph.to_string(), fresh);
        } else {
            self.schemas.insert(graph.to_string(), schema);
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn params_header_matches_falkordb_py() {
        let mut params = BTreeMap::new();
        params.insert("name".to_string(), Param::Str("O'Brien".into()));
        params.insert("limit".to_string(), Param::Int(5));
        params.insert("scope".to_string(), Param::Null);
        params.insert("flag".to_string(), Param::Bool(true));
        params.insert("tags".to_string(), Param::List(vec!["a".into(), "b".into()]));
        let header = build_params_header(&params);
        // Thứ tự BTreeMap: flag, limit, name, scope, tags
        assert_eq!(
            header,
            "CYPHER `flag`=True `limit`=5 `name`=\"O'Brien\" `scope`=null `tags`=[\"a\",\"b\"] "
        );
    }

    #[test]
    fn empty_params_no_header() {
        let params: BTreeMap<String, Param> = BTreeMap::new();
        assert_eq!(build_params_header(&params), "");
    }

    #[test]
    fn statistic_parsing() {
        let result = QueryResult {
            header: vec![],
            records: vec![],
            statistics: vec![
                "Nodes created: 1".to_string(),
                "Properties set: 3".to_string(),
                "Query internal execution time: 0.249 milliseconds".to_string(),
            ],
        };
        assert_eq!(result.statistic("Nodes created"), Some(1));
        assert_eq!(result.statistic("Properties set"), Some(3));
        assert_eq!(result.statistic("Labels added"), None);
        assert_eq!(result.internal_execution_time_ms(), Some(0.249));
    }
}

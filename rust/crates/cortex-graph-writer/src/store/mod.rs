//! Driver abstraction — port của `tools/graph/core/base.py::GraphDriver`
//! (phần writer cần) với 2 backend như phase-03.md yêu cầu:
//!
//! * [`falkordb_store::FalkorDbStore`] — remote FalkorDB (crate `cortex-falkordb`,
//!   GRAPH.RO_QUERY/GRAPH.QUERY --compact, param header kiểu falkordb-py);
//! * [`ladybug_store::LadybugStore`] — embedded LadybugDB (crate `lbug`),
//!   static schema + bootstrap + auto-DDL + dialect rewrites.
//!
//! Provider selection theo env như `GraphDriverFactory` của Python — xem
//! [`open_store_from_env`].

pub mod falkordb_store;
pub mod ladybug_store;

use std::collections::BTreeMap;

use serde_json::Value;

use crate::json_row::Row;

/// Một index definition từ schema manifest (`driver_indexes` của
/// `GraphSchemaManifest`): label + property + index_type.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IndexSpec {
    pub label: String,
    pub property: String,
    pub index_type: String,
}

/// Lỗi store — bọc lỗi 2 backend + preflight.
#[derive(Debug)]
pub enum StoreError {
    Falkor(cortex_falkordb::client::ClientError),
    Ladybug(String),
    /// Query bị classify là schema-shaped nhưng auto-DDL không dựng được
    /// statement (fail-closed như Python).
    Schema(String),
    Io(std::io::Error),
    Invalid(String),
}

impl std::fmt::Display for StoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StoreError::Falkor(e) => write!(f, "falkordb: {e}"),
            StoreError::Ladybug(m) => write!(f, "ladybug: {m}"),
            StoreError::Schema(m) => write!(f, "schema: {m}"),
            StoreError::Io(e) => write!(f, "io: {e}"),
            StoreError::Invalid(m) => write!(f, "invalid: {m}"),
        }
    }
}

impl std::error::Error for StoreError {}

impl From<cortex_falkordb::client::ClientError> for StoreError {
    fn from(e: cortex_falkordb::client::ClientError) -> Self {
        StoreError::Falkor(e)
    }
}

impl From<std::io::Error> for StoreError {
    fn from(e: std::io::Error) -> Self {
        StoreError::Io(e)
    }
}

/// Kết quả `execute_query` — records dạng JSON map (khớp `(records, keys,
/// summary)` của Python; writer chỉ đọc `count` từ records nên summary không
/// cần expose).
pub type QueryRecords = Vec<Row>;

/// Contract mà writer/preflight dùng — tương đương mặt `execute_query`,
/// `ensure_schema`, `create_indexes`, `inspect_indexes` của `GraphDriver`.
pub trait GraphStore {
    /// Tên provider (`falkordb` / `ladybug`) — khớp `GraphProvider.value`.
    fn provider(&self) -> &'static str;

    /// Thực thi một query; `parameters` là params map (JSON values).
    fn execute_query(
        &mut self,
        query: &str,
        parameters: &BTreeMap<String, Value>,
        database: Option<&str>,
    ) -> Result<QueryRecords, StoreError>;

    /// `GraphDriver.ensure_schema` — preflight index/bootstrap.
    fn ensure_schema(&mut self, database: Option<&str>) -> Result<(), StoreError>;

    /// Index metadata chuẩn hoá cho preflight (`inspect_indexes`).
    fn inspect_indexes(
        &mut self,
        database: Option<&str>,
    ) -> Result<Vec<BTreeMap<String, Value>>, StoreError>;

    /// Tạo index (`create_indexes`); idempotent theo từng backend.
    fn create_indexes(&mut self, indexes: &[IndexSpec], database: Option<&str>)
        -> Result<(), StoreError>;
}

pub use falkordb_store::FalkorDbStore;
pub use ladybug_store::LadybugStore;

/// Env keys dùng bởi [`open_store_from_env`].
pub const ENV_PROVIDER: &str = "CORTEX_GRAPH_PROVIDER";
pub const ENV_FALKORDB_HOST: &str = "CORTEX_FALKORDB_HOST";
pub const ENV_FALKORDB_PORT: &str = "CORTEX_FALKORDB_PORT";
pub const ENV_FALKORDB_GRAPH: &str = "CORTEX_FALKORDB_GRAPH";
pub const ENV_LADYBUG_PATH: &str = "CORTEX_LADYBUG_PATH";
pub const ENV_LADYBUG_GRAPH: &str = "CORTEX_LADYBUG_GRAPH";

fn env_or(name: &str, default: &str) -> String {
    std::env::var(name)
        .ok()
        .filter(|v| !v.trim().is_empty())
        .unwrap_or_else(|| default.to_string())
}

/// Provider selection theo env — tương đương `GraphDriverFactory` của Python
/// (default: falkordb remote trên POSIX như dây chuyền hiện hành; ladybug
/// opt-in qua `CORTEX_GRAPH_PROVIDER=ladybug` + `CORTEX_LADYBUG_PATH`).
pub fn open_store_from_env() -> Result<Box<dyn GraphStore>, StoreError> {
    let provider = env_or(ENV_PROVIDER, "falkordb").to_lowercase();
    match provider.as_str() {
        "falkordb" => {
            let host = env_or(ENV_FALKORDB_HOST, "127.0.0.1");
            let port: u16 = env_or(ENV_FALKORDB_PORT, "6379")
                .parse()
                .map_err(|_| StoreError::Invalid("CORTEX_FALKORDB_PORT không hợp lệ".into()))?;
            let graph = env_or(ENV_FALKORDB_GRAPH, "stock");
            let client = cortex_falkordb::client::FalkorDbClient::connect_verified(&host, port)?;
            Ok(Box::new(FalkorDbStore::new(client, graph)))
        }
        "ladybug" => {
            let path = env_or(ENV_LADYBUG_PATH, "");
            if path.is_empty() {
                return Err(StoreError::Invalid(
                    "CORTEX_LADYBUG_PATH là bắt buộc khi provider=ladybug".into(),
                ));
            }
            let graph = env_or(ENV_LADYBUG_GRAPH, "hyper_graph");
            Ok(Box::new(LadybugStore::open(
                std::path::Path::new(&path),
                &graph,
            )?))
        }
        other => Err(StoreError::Invalid(format!(
            "unsupported CORTEX_GRAPH_PROVIDER: {other}"
        ))),
    }
}

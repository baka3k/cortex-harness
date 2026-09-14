// Faithful port của Python bodies: giữ cấu trúc nguồn để đối chiếu parity;
// các lint style dưới đây được allow có chủ đích ở module graph.
#![allow(
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::unnecessary_filter_map,
    clippy::needless_borrow,
    clippy::map_clone,
    clippy::obfuscated_if_else,
    clippy::unnecessary_lazy_evaluations,
    clippy::let_and_return,
    clippy::useless_format,
    clippy::manual_strip,
    clippy::unwrap_or_default
)]
//! Embedded FalkorDB runtime cho phase-12 graph tools.
//!
//! Port của phần network IO mà `cplus_mcp._get_graph_driver` +
//! `tools/graph/driver/falkordb_driver.py` đứng sau:
//!
//! * `falkordb_discovery.discover_falkordb_data_files()` — quét mọi
//!   `data.rdb` dưới `<CORTEX_DATA_HOME|~/.cortext-harness>/v1/instances/*/falkordb/code/`.
//! * `redislite` boot: spawn `redis-server` (binary đi kèm package
//!   `redislite` trong repo venv) với config `dbdir/dbfilename` trỏ vào
//!   `data.rdb` + `--loadmodule falkordb.so`, connect qua TCP localhost
//!   (redislite của Python dùng unix socket; Rust client dùng TCP — cùng
//!   engine, cùng dữ liệu trong bộ nhớ).
//! * Server tắt bằng `SHUTDOWN NOSAVE` — **không bao giờ ghi** vào
//!   `data.rdb` của instance (read-only fan-out như sibling clients của
//!   driver Python).
//! * `_graph_clients`: graph name → client (primary path thắng khi trùng).
//!
//! Kết quả query được chuẩn hoá qua `cortex_falkordb::normalize::normalize_value`
//! — trùng `falkordb_driver._normalize_falkordb_value` — rồi gói thành records
//! dạng `Vec<JsonMap>` theo header names (như `dict(zip(keys, row))`).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Child;
use std::sync::Mutex;

use cortex_falkordb::client::{Column, FalkorDbClient, Param, QueryResult};
use cortex_falkordb::normalize::normalize_value;
use serde_json::{Map, Number, Value};


// ---------------------------------------------------------------------------
// Discovery (falkordb_discovery.py)
// ---------------------------------------------------------------------------

/// `falkordb_discovery._data_root()`.
fn data_root() -> PathBuf {
    if let Ok(home) = std::env::var("CORTEX_DATA_HOME") {
        let trimmed = home.trim();
        if !trimmed.is_empty() {
            let expanded = if trimmed.starts_with('~') {
                if let Some(home_dir) = std::env::var_os("HOME") {
                    Path::new(&home_dir).join(&trimmed[1..])
                } else {
                    PathBuf::from(trimmed)
                }
            } else {
                PathBuf::from(trimmed)
            };
            return expanded;
        }
    }
    match std::env::var("HOME") {
        Ok(home) => Path::new(&home).join(".cortext-harness"),
        Err(_) => PathBuf::from(".cortext-harness"),
    }
}

/// `discover_falkordb_data_files()` — primary instance trước (storage layout
/// từ cwd + `CORTEX_STORAGE_INSTANCE`), sau đó siblings theo thứ tự sort.
pub fn discover_falkordb_data_files() -> Vec<PathBuf> {
    let instances_root = data_root().join("v1").join("instances");
    let mut primary: Option<PathBuf> = None;
    let self_id = std::env::var("CORTEX_STORAGE_INSTANCE").unwrap_or_default();
    let self_id = if self_id.is_empty() { "default".to_string() } else { self_id };
    let self_candidate = instances_root
        .join(&self_id)
        .join("falkordb")
        .join("code")
        .join("data.rdb");
    if self_candidate.is_file() {
        primary = Some(self_candidate);
    }
    let mut files: Vec<PathBuf> = Vec::new();
    let Ok(entries) = std::fs::read_dir(&instances_root) else {
        return primary.into_iter().collect();
    };
    let mut dirs: Vec<PathBuf> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.is_dir())
        .collect();
    dirs.sort();
    for dir in dirs {
        let candidate = dir
            .join("falkordb")
            .join("code")
            .join("data.rdb");
        if candidate.is_file() {
            files.push(candidate);
        }
    }
    match primary {
        Some(primary) => {
            let mut ordered = vec![primary];
            for file in files {
                if !ordered.contains(&file) {
                    ordered.push(file);
                }
            }
            ordered
        }
        None => files,
    }
}

/// `ladybug_discovery.build_ladybug_driver_config()` — phần discovery data
/// files (config còn lại chỉ là env plumbing cho driver Python).
pub fn discover_ladybug_store_files() -> Vec<PathBuf> {
    let instances_root = data_root().join("v1").join("instances");
    let mut files: Vec<PathBuf> = Vec::new();
    let Ok(entries) = std::fs::read_dir(&instances_root) else {
        return files;
    };
    let self_id = std::env::var("CORTEX_STORAGE_INSTANCE").unwrap_or_default();
    let mut dirs: Vec<PathBuf> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.is_dir())
        .collect();
    dirs.sort();
    for dir in dirs {
        if !self_id.is_empty()
            && dir
                .file_name()
                .map(|name| name.to_string_lossy() == self_id)
                .unwrap_or(false)
        {
            continue;
        }
        let owner_root = dir.join("ladybug").join("code");
        let Ok(stores) = std::fs::read_dir(&owner_root) else {
            continue;
        };
        let mut store_dirs: Vec<PathBuf> = stores
            .flatten()
            .map(|entry| entry.path())
            .filter(|path| {
                path.is_dir()
                    && path
                        .extension()
                        .map(|ext| ext == "lbug")
                        .unwrap_or(false)
            })
            .collect();
        store_dirs.sort();
        for store_dir in store_dirs {
            let Ok(graphs) = std::fs::read_dir(&store_dir) else {
                continue;
            };
            let mut graph_files: Vec<PathBuf> = graphs
                .flatten()
                .map(|entry| entry.path())
                .filter(|path| path.is_file())
                .collect();
            graph_files.sort();
            files.extend(graph_files);
        }
    }
    files
}

// ---------------------------------------------------------------------------
// redislite launch
// ---------------------------------------------------------------------------

struct EmbeddedServer {
    child: Child,
    port: u16,
}

impl Drop for EmbeddedServer {
    fn drop(&mut self) {
        // Read-only contract: SHUTDOWN NOSAVE để không bao giờ đè data.rdb.
        let cli = redislite_bin("redis-cli").unwrap_or_else(|| PathBuf::from("redis-cli"));
        let _ = std::process::Command::new(cli)
            .args([
                "-h",
                "127.0.0.1",
                "-p",
                &self.port.to_string(),
                "SHUTDOWN",
                "NOSAVE",
            ])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
        // Grace ≤ 2s rồi kill -9 — không bao giờ block shutdown của server.
        for _ in 0..20 {
            if let Ok(Some(_)) = self.child.try_wait() {
                return;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

// `port` cần truy cập được từ struct — giữ Port vừa cấp phát.
struct EmbeddedFalkor {
    _server: EmbeddedServer,
    port: u16,
}

/// Resolve redislite binaries: `.venv/lib/python3.*/site-packages/redislite/bin/`.
fn redislite_bin(name: &str) -> Option<PathBuf> {
    // CARGO_MANIFEST_DIR = <repo>/rust/crates/cortex-mcp → repo root 3 cấp trên.
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(std::path::Path::parent)
        .and_then(std::path::Path::parent)
        .map(std::path::Path::to_path_buf)?;
    let venv_bin = repo.join(".venv").join("bin").join(name);
    if venv_bin.is_file() {
        return Some(venv_bin);
    }
    // redislite package layout.
    let venv = repo.join(".venv").join("lib");
    let Ok(pythons) = std::fs::read_dir(&venv) else {
        return None;
    };
    for python in pythons.flatten() {
        let candidate = python
            .path()
            .join("site-packages")
            .join("redislite")
            .join("bin")
            .join(name);
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

fn bind_free_tcp_port() -> Option<u16> {
    std::net::TcpListener::bind(("127.0.0.1", 0))
        .ok()
        .and_then(|listener| listener.local_addr().ok())
        .map(|addr| addr.port())
}

fn launch_embedded_falkordb(data_rdb: &Path) -> Result<EmbeddedFalkor, String> {
    let redis_server = redislite_bin("redis-server")
        .ok_or_else(|| "redislite redis-server binary not found".to_string())?;
    let falkordb_so = redislite_bin("falkordb.so")
        .ok_or_else(|| "redislite falkordb.so module not found".to_string())?;
    let dbdir = data_rdb
        .parent()
        .ok_or_else(|| "data.rdb has no parent".to_string())?;
    let dbfilename = data_rdb
        .file_name()
        .map(|name| name.to_string_lossy().to_string())
        .ok_or_else(|| "data.rdb has no file name".to_string())?;
    let port = bind_free_tcp_port().ok_or_else(|| "no free TCP port".to_string())?;

    let temp_dir = std::env::temp_dir().join(format!(
        "cortex-mcp-falkordb-{}-{}",
        std::process::id(),
        port
    ));
    std::fs::create_dir_all(&temp_dir).map_err(|error| format!("mkdir: {error}"))?;
    let config_path = temp_dir.join("redis.config");
    let logfile = temp_dir.join("redis.log");
    // Minimal redis config — redislite viết cùng các key (pidfile/logfile/
    // unixsocket/dbdir/dbfilename); Rust thay unixsocket bằng TCP port.
    let config = format!(
        "port {port}\nbind 127.0.0.1\ndir {dbdir}\ndbfilename {dbfilename}\n\
         save \"\"\nappendonly no\nlogfile {logfile}\ndaemonize no\n",
        dbdir = dbdir.display(),
        logfile = logfile.display(),
    );
    std::fs::write(&config_path, config).map_err(|error| format!("write config: {error}"))?;

    let mut command = std::process::Command::new(&redis_server);
    command.arg(&config_path);
    command.args(["--loadmodule"]).arg(&falkordb_so);
    let child = command
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|error| format!("spawn redis-server: {error}"))?;

    let embedded = EmbeddedFalkor {
        port,
        _server: EmbeddedServer { child, port },
    };

    // Đợi server sẵn sàng (PING qua client FalkorDB).
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(60);
    loop {
        if std::time::Instant::now() > deadline {
            return Err(format!("embedded FalkorDB on :{port} failed to boot (timeout)"));
        }
        if let Ok(mut client) = FalkorDbClient::connect("127.0.0.1", embedded.port) {
            if raw_graph_list(&mut client).is_ok() {
                break;
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(250));
    }
    Ok(embedded)
}

/// `client.list_graphs()` — `GRAPH.LIST` qua connection của FalkorDbClient.
fn raw_graph_list(client: &mut FalkorDbClient) -> Result<Vec<String>, String> {
    let names: Vec<String> = redis::cmd("GRAPH.LIST")
        .query(client.connection_mut())
        .map_err(|error| error.to_string())?;
    Ok(names)
}

// ---------------------------------------------------------------------------
// Runtime: graph name → client
// ---------------------------------------------------------------------------

pub struct GraphRuntime {
    /// `data.rdb path → (client, embedded server handle)`.
    clients: Vec<(PathBuf, FalkorDbClient, EmbeddedFalkor)>,
    /// graph name → client index, theo thứ tự đăng ký (primary file wins).
    graph_clients: Vec<(String, usize)>,
    /// Default graph (`FALKORDB_GRAPH` env của unified/cplus boot).
    pub default_graph: String,
}

impl GraphRuntime {
    /// Boot giống `cplus_mcp._get_graph_driver()` provider=falkordb:
    /// primary = storage-resolved instance path, siblings từ discovery.
    pub fn boot() -> Result<Self, String> {
        let default_graph = std::env::var("FALKORDB_GRAPH")
            .or_else(|_| std::env::var("FALKORDB_DATABASE"))
            .unwrap_or_else(|_| "hyper_graph".to_string());
        let files = discover_falkordb_data_files();
        if files.is_empty() {
            // Không có data file nào: runtime rỗng (mọi query → lỗi db) —
            // mirror driver không list được graph nào.
            return Ok(Self {
                clients: Vec::new(),
                graph_clients: Vec::new(),
                default_graph,
            });
        }
        let mut clients: Vec<(PathBuf, FalkorDbClient, EmbeddedFalkor)> = Vec::new();
        let mut graph_clients: Vec<(String, usize)> = Vec::new();
        for path in &files {
            let embedded = launch_embedded_falkordb(path).map_err(|error| {
                format!("instance {}: {error}", path.display())
            })?;
            let mut client = match FalkorDbClient::connect("127.0.0.1", embedded.port) {
                Ok(client) => client,
                Err(error) => {
                    return Err(format!("connect {}: {error}", path.display()));
                }
            };
            match raw_graph_list(&mut client) {
                Ok(names) => {
                    for name in names {
                        if !graph_clients.iter().any(|(existing, _)| existing == &name) {
                            graph_clients.push((name, clients.len()));
                        }
                    }
                }
                Err(error) => {
                    return Err(format!("list_graphs {}: {error}", path.display()));
                }
            }
            clients.push((path.clone(), client, embedded));
        }
        Ok(Self {
            clients,
            graph_clients,
            default_graph,
        })
    }

    pub fn graph_names(&self) -> Vec<String> {
        self.graph_clients
            .iter()
            .map(|(name, _)| name.clone())
            .collect()
    }

    /// `driver.list_databases()` — graph names; rỗng → [default].
    pub fn list_databases(&self) -> Vec<String> {
        let names = self.graph_names();
        if names.is_empty() {
            vec![self.default_graph.clone()]
        } else {
            names
        }
    }

    /// Sync query — chạy trong `spawn_blocking` phía caller.
    pub fn execute_query(
        &mut self,
        cypher: &str,
        params: &BTreeMap<String, Param>,
        database: Option<&str>,
    ) -> Result<Vec<Map<String, Value>>, String> {
        let graph_name = database
            .map(str::to_string)
            .unwrap_or_else(|| self.default_graph.clone());
        let index = self
            .graph_clients
            .iter()
            .find(|(name, _)| name == &graph_name)
            .map(|(_, index)| *index);
        let result: Result<QueryResult, cortex_falkordb::client::ClientError> = match index {
            Some(index) => self.clients[index].1.ro_query(&graph_name, cypher, params),
            None => Err(cortex_falkordb::client::ClientError::Server(format!(
                "Unknown graph: {graph_name}"
            ))),
        };
        let result = result.map_err(|error| error.to_string())?;
        Ok(records_to_maps(&result))
    }
}

/// Header names + normalized values → records (Python `dict(zip(keys, row))`).
fn records_to_maps(result: &QueryResult) -> Vec<Map<String, Value>> {
    let names: Vec<String> = result
        .header
        .iter()
        .map(|column: &Column| column.name.clone())
        .collect();
    result
        .records
        .iter()
        .map(|row| {
            let mut map = Map::new();
            for (index, value) in row.iter().enumerate() {
                let key = names
                    .get(index)
                    .cloned()
                    .unwrap_or_else(|| format!("column_{index}"));
                map.insert(key, normalize_value(value));
            }
            map
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Param conversion (serde_json → wire Param)
// ---------------------------------------------------------------------------

/// `stringify_param_value` head: JSON value → Param.
pub fn json_to_param(value: &Value) -> Param {
    match value {
        Value::Null => Param::Null,
        Value::Bool(flag) => Param::Bool(*flag),
        Value::Number(number) => {
            if let Some(int) = number.as_i64() {
                Param::Int(int)
            } else {
                Param::Float(number.as_f64().unwrap_or(0.0))
            }
        }
        Value::String(text) => Param::Str(text.clone()),
        Value::Array(items) => Param::List(items.iter().map(json_to_param).collect()),
        Value::Object(map) => Param::Map(
            map.iter()
                .map(|(key, value)| (key.clone(), json_to_param(value)))
                .collect(),
        ),
    }
}

// ---------------------------------------------------------------------------
// Global runtime singleton
// ---------------------------------------------------------------------------

static RUNTIME: Mutex<Option<GraphRuntime>> = Mutex::new(None);

/// Boot-once shared runtime (global `_graph_driver` của backend Python).
/// Boot thất bại → trả lỗi; caller dựng error envelope như Python
/// (`_get_graph_driver` raise → `_build_tool_error`).
pub fn with_graph_runtime<T>(
    operation: impl FnOnce(&mut GraphRuntime) -> Result<T, String>,
) -> Result<T, String> {
    let mut guard = RUNTIME.lock().map_err(|_| "runtime lock poisoned")?;
    if guard.is_none() {
        let runtime = GraphRuntime::boot()?;
        *guard = Some(runtime);
    }
    operation(guard.as_mut().expect("booted"))
}

/// Graceful cleanup: SHUTDOWN NOSAVE cho mọi embedded server khi server
/// процесс tắt (Drop của static không chạy khi SIGTERM kill process).
pub fn shutdown_runtime() {
    if let Ok(mut guard) = RUNTIME.lock() {
        if guard.is_some() {
            *guard = None;
        }
    }
}

/// Node labels (`CALL db.labels()`) — dùng bởi inspect_parser_capabilities +
/// `_resolve_direct_capability_context`.
pub fn list_node_labels(
    runtime: &mut GraphRuntime,
    dbs: &[String],
) -> Option<Vec<String>> {
    let mut collected: Option<Vec<String>> = None;
    for db in dbs.iter().filter(|item| !item.is_empty()) {
        let query = "CALL db.labels() YIELD label RETURN label AS label";
        let params = BTreeMap::new();
        let rows = match runtime.execute_query(query, &params, Some(db)) {
            Ok(rows) => rows,
            Err(error) => {
                if is_missing_graph_error(&error) {
                    // Python driver: query vào graph chưa tồn tại được
                    // falkordb auto-create (empty) → success với [] —
                    // không phải db-not-found skip.
                    if collected.is_none() {
                        collected = Some(Vec::new());
                    }
                    continue;
                }
                if collected.is_none() {
                    continue;
                }
                break;
            }
        };
        if collected.is_none() {
            collected = Some(Vec::new());
        }
        let collected_ref = collected.as_mut().expect("initialized");
        for row in rows {
            if let Some(label) = row.get("label").and_then(Value::as_str) {
                // Python giữ nguyên case label từ `CALL db.labels()`.
                if !collected_ref.contains(&label.to_string()) {
                    collected_ref.push(label.to_string());
                }
            }
        }
    }
    collected
}

/// `CALL db.relationshipTypes()` — union uppercase; None khi inspect fail.
pub fn list_relationship_types(
    runtime: &mut GraphRuntime,
    dbs: &[String],
) -> Option<Vec<String>> {
    let mut collected: Option<Vec<String>> = None;
    for db in dbs.iter().filter(|item| !item.is_empty()) {
        let query =
            "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType AS rel_type";
        let params = BTreeMap::new();
        let rows = match runtime.execute_query(query, &params, Some(db)) {
            Ok(rows) => rows,
            Err(error) => {
                if is_missing_graph_error(&error) {
                    // Python driver: graph chưa tồn tại → auto-create empty
                    // → success với [] (xem list_node_labels).
                    if collected.is_none() {
                        collected = Some(Vec::new());
                    }
                    continue;
                }
                if collected.is_none() {
                    continue;
                }
                break;
            }
        };
        if collected.is_none() {
            collected = Some(Vec::new());
        }
        let collected_ref = collected.as_mut().expect("initialized");
        for row in rows {
            if let Some(rel_type) = row.get("rel_type").and_then(Value::as_str) {
                let upper = rel_type.to_uppercase();
                if !collected_ref.contains(&upper) {
                    collected_ref.push(upper);
                }
            }
        }
    }
    collected
}

/// Lỗi "graph không tồn tại" từ pre-check của `GraphRuntime::execute_query`
/// hoặc từ server — tương đương nhánh `_is_db_not_found` của Python.
fn is_missing_graph_error(message: &str) -> bool {
    super::is_database_not_found_error(message)
}

/// `_normalize_db_name` — basename cho path-shaped names + `.db.db` strip.
pub fn normalize_db_name(value: &str) -> String {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return trimmed.to_string();
    }
    let looks_pathy = trimmed.starts_with('/') || trimmed.contains('/') || trimmed.contains('\\');
    let mut name = if looks_pathy {
        Path::new(trimmed)
            .file_name()
            .map(|name| name.to_string_lossy().to_string())
            .unwrap_or_else(|| trimmed.to_string())
    } else {
        trimmed.to_string()
    };
    while name.ends_with(".db.db") {
        name.truncate(name.len() - 3);
    }
    name
}

/// `json_to_number` — Python `int(value)` với fallback None.
pub fn json_int(value: Option<&Value>, default: i64) -> i64 {
    match value {
        Some(Value::Number(number)) => {
            if let Some(int) = number.as_i64() {
                int
            } else {
                number.as_f64().map(|float| float as i64).unwrap_or(default)
            }
        }
        Some(Value::String(text)) => text.trim().parse().unwrap_or(default),
        Some(Value::Bool(flag)) => {
            if *flag {
                1
            } else {
                0
            }
        }
        _ => default,
    }
}

/// JSON number helper cho responses (giữ int khi nguyên).
pub fn json_f64(value: f64) -> Value {
    Number::from_f64(value).map(Value::Number).unwrap_or(Value::Null)
}

/// Slice helper cho `run_cypher_first` dbs param.
pub fn dbs_slice(database: Option<&str>) -> Vec<String> {
    database.map(str::to_string).into_iter().collect()
}

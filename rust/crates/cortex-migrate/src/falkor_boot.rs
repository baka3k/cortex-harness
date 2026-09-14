//! Embedded FalkorDBLite boot cho migration — được cop từ cơ chế đã chạy của
//! `cortex-mcp/src/graph/runtime.rs` (phase 12): spawn `redis-server` đi kèm
//! package `redislite` của repo venv với config `dbdir/dbfilename` trỏ vào
//! `data.rdb` + `--loadmodule falkordb.so`, connect qua TCP localhost.
//!
//! Read-only contract giữ nguyên: server tắt bằng `SHUTDOWN NOSAVE` — **không
//! bao giờ ghi** vào `data.rdb` của instance. Khác biệt duy nhất so với
//! runtime.rs: temp dir prefix `cortex-migrate-*` để dễ trace trong `ps`.

use std::path::{Path, PathBuf};
use std::process::Child;

use cortex_falkordb::client::FalkorDbClient;

/// Resolve redislite binaries: `.venv/lib/python3.*/site-packages/redislite/bin/`
/// (cơ chế của `runtime.rs::redislite_bin`).
pub fn redislite_bin(name: &str) -> Option<PathBuf> {
    // CARGO_MANIFEST_DIR = <repo>/rust/crates/cortex-migrate → repo root 3 cấp trên.
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(std::path::Path::parent)
        .and_then(std::path::Path::parent)
        .map(std::path::Path::to_path_buf)?;
    let venv_bin = repo.join(".venv").join("bin").join(name);
    if venv_bin.is_file() {
        return Some(venv_bin);
    }
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

struct EmbeddedServer {
    child: Child,
    port: u16,
}

impl Drop for EmbeddedServer {
    fn drop(&mut self) {
        // SHUTDOWN NOSAVE — không đè data.rdb (read-only contract).
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

/// Một server FalkorDBLite đang chạy trên `data.rdb`. Drop ⇒ SHUTDOWN NOSAVE.
pub struct EmbeddedFalkor {
    _server: EmbeddedServer,
    port: u16,
}

impl EmbeddedFalkor {
    /// Client đã connect sẵn (dùng cho mọi read của migration).
    pub fn connect(&self) -> Result<FalkorDbClient, String> {
        FalkorDbClient::connect("127.0.0.1", self.port)
            .map_err(|error| format!("connect 127.0.0.1:{}: {error}", self.port))
    }

}

/// Boot redislite + falkordb.so trên một `data.rdb` (giống
/// `runtime.rs::launch_embedded_falkordb`, gồm PING/GRAPH.LIST readiness).
pub fn launch_embedded_falkordb(data_rdb: &Path) -> Result<EmbeddedFalkor, String> {
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
        "cortex-migrate-falkordb-{}-{}",
        std::process::id(),
        port
    ));
    std::fs::create_dir_all(&temp_dir).map_err(|error| format!("mkdir: {error}"))?;
    let config_path = temp_dir.join("redis.config");
    let logfile = temp_dir.join("redis.log");
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

    // Đợi server sẵn sàng (GRAPH.LIST trả lời qua client FalkorDB).
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(60);
    loop {
        if std::time::Instant::now() > deadline {
            return Err(format!(
                "embedded FalkorDB on :{port} failed to boot (timeout)"
            ));
        }
        if let Ok(mut client) = FalkorDbClient::connect("127.0.0.1", embedded.port)
            && graph_list(&mut client).is_ok()
        {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(250));
    }
    Ok(embedded)
}

/// `GRAPH.LIST` qua connection thô của client (như `runtime.rs::raw_graph_list`).
pub fn graph_list(client: &mut FalkorDbClient) -> Result<Vec<String>, String> {
    let names: Vec<String> = redis::cmd("GRAPH.LIST")
        .query(client.connection_mut())
        .map_err(|error| error.to_string())?;
    Ok(names)
}

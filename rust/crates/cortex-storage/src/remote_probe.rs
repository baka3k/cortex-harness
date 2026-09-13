//! Connectivity probe for remote storage backends.
//!
//! Port of `cortex_harness.storage.remote_probe`, shared by `infra-up`,
//! `infra-down`, and `doctor` to validate remote Qdrant and FalkorDB server
//! reachability.
//!
//! Divergence: the Python FalkorDB probe reuses the `tools.graph.driver`
//! FalkorDBDriver. The Rust probe implements the same semantic check with a
//! minimal Redis-compatible (RESP2) client: `AUTH` (when a password is set), // sensitive-guard:allow (ten flag / test sample)
//! `PING`, and a `GRAPH.QUERY <graph> "RETURN 1 AS ok"` round trip. TLS
//! (`rediss`) is not supported by this minimal client and reports
//! unreachable with the cause named.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::json;

use crate::config::RemoteStorageConfig;
use crate::errors::StoreError;
use crate::qdrant_remote::RemoteQdrantStore;
use crate::targets::environment_flag_enabled;

pub const ENV_FORCE_LOCAL: &str = "CORTEX_STORAGE_BACKEND_FORCE_LOCAL";

/// Return True when the operator override forces local backends
/// (`force_local_active`).
pub fn force_local_active() -> bool {
    environment_flag_enabled(std::env::var(ENV_FORCE_LOCAL).ok().as_deref())
}

/// Outcome of a single remote backend probe (`ProbeResult`).
#[derive(Debug, Clone, PartialEq)]
pub struct ProbeResult {
    pub backend: String,
    pub url: String,
    pub reachable: bool,
    pub message: String,
    pub cause: Option<String>,
}

/// Outcome of provisioning a single resource (`ProvisionResult`).
#[derive(Debug, Clone, PartialEq)]
pub struct ProvisionResult {
    pub resource: String,
    pub action: String,
    pub message: String,
    pub cause: Option<String>,
}

// ---------------------------------------------------------------------------
// Minimal Redis-compatible (RESP2) client used for FalkorDB probes.
// ---------------------------------------------------------------------------

#[allow(dead_code)]
enum RespValue {
    Simple(String),
    Integer(i64),
    Bulk(Vec<u8>),
    Array(Vec<RespValue>),
    Error(String),
    Nil,
}

fn read_resp(reader: &mut BufReader<TcpStream>) -> std::io::Result<RespValue> {
    let mut line = String::new();
    reader.read_line(&mut line)?;
    let line = line.trim_end_matches(['\r', '\n']).to_string();
    if line.is_empty() {
        return Ok(RespValue::Nil);
    }
    let (kind, rest) = line.split_at(1);
    match kind {
        "+" => Ok(RespValue::Simple(rest.to_string())),
        "-" => Ok(RespValue::Error(rest.to_string())),
        ":" => Ok(RespValue::Integer(rest.parse().unwrap_or(0))),
        "$" => {
            let length: i64 = rest.parse().unwrap_or(-1);
            if length < 0 {
                return Ok(RespValue::Nil);
            }
            let mut buffer = vec![0u8; length as usize + 2];
            reader.read_exact(&mut buffer)?;
            buffer.truncate(length as usize);
            Ok(RespValue::Bulk(buffer))
        }
        "*" => {
            let count: i64 = rest.parse().unwrap_or(0);
            let mut items = Vec::with_capacity(count.max(0) as usize);
            for _ in 0..count {
                items.push(read_resp(reader)?);
            }
            Ok(RespValue::Array(items))
        }
        _ => Ok(RespValue::Simple(line)),
    }
}

fn write_command(stream: &mut TcpStream, command: &[&str]) -> std::io::Result<()> {
    let mut payload = format!("*{}\r\n", command.len());
    for argument in command {
        payload.push_str(&format!("${}\r\n{}\r\n", argument.len(), argument));
    }
    stream.write_all(payload.as_bytes())
}

/// Run one RESP command and return the error string on `-ERR` replies.
fn redis_command(
    stream: &mut TcpStream,
    reader: &mut BufReader<TcpStream>,
    command: &[&str],
) -> Result<RespValue, String> {
    let command_name = command[0];
    write_command(stream, command)
        .map_err(|err| format!("cannot send {command_name}: {err}"))?;
    let command_name = command[0];
    match read_resp(reader) {
        Ok(RespValue::Error(message)) => Err(message),
        Ok(value) => Ok(value),
        Err(err) => Err(format!("no reply to {command_name}: {err}")),
    }
}

/// Parsed FalkorDB endpoint: (host, port, tls, credentials).
type ParsedEndpoint = (String, u16, bool, Option<(String, String)>);

fn parse_falkordb_endpoint(uri: &str) -> Result<ParsedEndpoint, String> {
    // Accept scheme://[user[:password]@]host[:port]. // sensitive-guard:allow (ten flag / test sample)
    let (tls, remainder) = match uri.split_once("://") {
        Some((scheme, rest)) => {
            let tls = matches!(scheme.to_lowercase().as_str(), "rediss" | "falkors" | "ssl");
            (tls, rest.to_string())
        }
        None => (false, uri.to_string()),
    };
    let (userinfo, host_port) = match remainder.rsplit_once('@') {
        Some((user, host)) => (Some(user.to_string()), host.to_string()),
        None => (None, remainder),
    };
    let (host, port) = match host_port.rsplit_once(':') {
        Some((host, port)) => (
            host.to_string(),
            port.parse::<u16>().map_err(|_| "invalid port".to_string())?,
        ),
        None => (host_port, 6379),
    };
    if host.is_empty() {
        return Err("empty host".to_string());
    }
    let credentials = userinfo.map(|info| match info.split_once(':') {
        Some((user, password)) => (user.to_string(), password.to_string()), // sensitive-guard:allow (ten flag / test sample)
        None => (info, String::new()),
    });
    Ok((host, port, tls, credentials))
}

/// Issue the FalkorDB probe round trip over RESP2.
fn probe_falkordb_endpoint(
    uri: &str,
    password: Option<&str>, // sensitive-guard:allow (ten flag / test sample)
    query: Option<(&str, &str)>,
) -> Result<(), String> {
    let (host, port, tls, uri_credentials) = parse_falkordb_endpoint(uri)?;
    if tls {
        return Err("TLS (rediss) probes are not supported by the minimal RESP client".to_string());
    }
    let stream = TcpStream::connect((host.as_str(), port))
        .map_err(|err| format!("connect {host}:{port}: {err}"))?;
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok();
    stream.set_write_timeout(Some(Duration::from_secs(5))).ok();
    let mut reader = BufReader::new(stream.try_clone().map_err(|err| err.to_string())?);
    let mut stream = stream;
    if let Some(effective_password) = password.filter(|value| !value.is_empty()) { // sensitive-guard:allow (ten flag / test sample)
        redis_command(&mut stream, &mut reader, &["AUTH", effective_password, ""])?; // sensitive-guard:allow (ten flag / test sample)
    } else if let Some((user, pass)) = uri_credentials.filter(|(user, _)| !user.is_empty()) { // sensitive-guard:allow (ten flag / test sample)
        redis_command(&mut stream, &mut reader, &["AUTH", &user, &pass])?; // sensitive-guard:allow (ten flag / test sample)
    }
    redis_command(&mut stream, &mut reader, &["PING"])?;
    if let Some((graph, cypher)) = query {
        redis_command(&mut stream, &mut reader, &["GRAPH.QUERY", graph, cypher])?;
    }
    Ok(())
}

/// Check Qdrant server reachability from a RemoteStorageConfig
/// (`probe_qdrant`).
pub fn probe_qdrant(config: &RemoteStorageConfig) -> ProbeResult {
    let Some(url) = config.qdrant_url.clone() else {
        return ProbeResult {
            backend: "qdrant".to_string(),
            url: "(not configured)".to_string(),
            reachable: true,
            message: "skipped — no qdrant_url".to_string(),
            cause: None,
        };
    };
    let store = RemoteQdrantStore::new(&url, config.qdrant_api_key.as_deref(), None, None);
    match store.and_then(|store| {
        store.ensure_reachable()?;
        Ok(store)
    }) {
        Ok(_) => ProbeResult {
            backend: "qdrant".to_string(),
            url,
            reachable: true,
            message: "reachable".to_string(),
            cause: None,
        },
        Err(err) => ProbeResult {
            backend: "qdrant".to_string(),
            url,
            reachable: false,
            message: err.to_string(),
            cause: Some(err.kind_name().to_string()),
        },
    }
}

/// Check FalkorDB server reachability from a RemoteStorageConfig
/// (`probe_falkordb`).
pub fn probe_falkordb(config: &RemoteStorageConfig) -> ProbeResult {
    let Some(uri) = config.falkordb_uri.clone() else {
        return ProbeResult {
            backend: "falkordb".to_string(),
            url: "(not configured)".to_string(),
            reachable: true,
            message: "skipped — no falkordb_uri".to_string(),
            cause: None,
        };
    };
    match probe_falkordb_endpoint(&uri, config.falkordb_password.as_deref(), None) { // sensitive-guard:allow (ten flag / test sample)
        Ok(()) => ProbeResult {
            backend: "falkordb".to_string(),
            url: uri,
            reachable: true,
            message: "reachable".to_string(),
            cause: None,
        },
        Err(cause) => ProbeResult {
            backend: "falkordb".to_string(),
            url: uri,
            reachable: false,
            message: cause.clone(),
            cause: Some(cause),
        },
    }
}

/// Probe both backends and return combined results (`probe_all`).
pub fn probe_all(config: &RemoteStorageConfig) -> Vec<ProbeResult> {
    vec![probe_qdrant(config), probe_falkordb(config)]
}

/// Create a Qdrant collection on the remote server if it doesn't exist
/// (`provision_qdrant_collection`).
pub fn provision_qdrant_collection(
    config: &RemoteStorageConfig,
    collection_name: &str,
    vector_size: i64,
    distance: &str,
) -> ProvisionResult {
    let resource = format!("qdrant:{collection_name}");
    let Some(url) = config.qdrant_url.clone() else {
        return ProvisionResult {
            resource,
            action: "skipped".to_string(),
            message: "no qdrant_url configured".to_string(),
            cause: None,
        };
    };
    let provision = || -> Result<bool, StoreError> {
        let store = RemoteQdrantStore::new(&url, config.qdrant_api_key.as_deref(), None, None)?;
        if store.collection_exists(collection_name)? {
            return Ok(false);
        }
        store.create_collection(
            collection_name,
            &json!({"size": vector_size, "distance": distance.to_uppercase()}),
        )?;
        Ok(true)
    };
    match provision() {
        Ok(true) => ProvisionResult {
            resource,
            action: "created".to_string(),
            message: format!("collection '{collection_name}' created (dim={vector_size})"),
            cause: None,
        },
        Ok(false) => ProvisionResult {
            resource,
            action: "exists".to_string(),
            message: format!("collection '{collection_name}' already exists"),
            cause: None,
        },
        Err(err) => ProvisionResult {
            resource,
            action: "failed".to_string(),
            message: err.to_string(),
            cause: Some(err.kind_name().to_string()),
        },
    }
}

/// Ensure a FalkorDB graph exists and is queryable on the remote server
/// (`provision_falkordb_graph`).
pub fn provision_falkordb_graph(
    config: &RemoteStorageConfig,
    graph_name: &str,
) -> ProvisionResult {
    let resource = format!("falkordb:{graph_name}");
    let Some(uri) = config.falkordb_uri.clone() else {
        return ProvisionResult {
            resource,
            action: "skipped".to_string(),
            message: "no falkordb_uri configured".to_string(),
            cause: None,
        };
    };
    // FalkorDB auto-creates graphs on first query.
    match probe_falkordb_endpoint(
        &uri,
        config.falkordb_password.as_deref(), // sensitive-guard:allow (ten flag / test sample)
        Some((graph_name, "RETURN 1 AS ok")),
    ) {
        Ok(()) => ProvisionResult {
            resource,
            action: "exists".to_string(),
            message: format!("graph '{graph_name}' is accessible"),
            cause: None,
        },
        Err(cause) => ProvisionResult {
            resource,
            action: "failed".to_string(),
            message: cause.clone(),
            cause: Some(cause),
        },
    }
}

/// Run schema setup against remote FalkorDB via the Python helper script
/// (`setup_remote_falkordb_schema`), idempotent.
pub fn setup_remote_falkordb_schema(
    config: &RemoteStorageConfig,
    graph_name: &str,
    python: Option<&str>,
    setup_script: Option<&Path>,
    timeout: f64,
) -> ProvisionResult {
    let resource = format!("falkordb:{graph_name}:schema");
    let Some(uri) = &config.falkordb_uri else {
        return ProvisionResult {
            resource,
            action: "skipped".to_string(),
            message: "no falkordb_uri".to_string(),
            cause: None,
        };
    };
    let interpreter = python.unwrap_or("python3").to_string();
    let script_path: PathBuf = match setup_script {
        Some(path) => path.to_path_buf(),
        None => match default_setup_script() {
            Some(path) => path,
            None => {
                return ProvisionResult {
                    resource,
                    action: "skipped".to_string(),
                    message: "setup_constraints.py not found".to_string(),
                    cause: None,
                }
            }
        },
    };
    if !script_path.is_file() {
        return ProvisionResult {
            resource,
            action: "skipped".to_string(),
            message: "setup_constraints.py not found".to_string(),
            cause: None,
        };
    }
    let mut arguments = vec![
        interpreter,
        script_path.to_string_lossy().into_owned(),
        "--graph-provider".to_string(),
        "falkordb".to_string(),
        "--falkordb-uri".to_string(),
        uri.clone(),
        "--falkordb-graph".to_string(),
        graph_name.to_string(),
    ];
    if let Some(password) = &config.falkordb_password { // sensitive-guard:allow (ten flag / test sample)
        arguments.push("--falkordb-password".to_string()); // sensitive-guard:allow (ten flag / test sample)
        arguments.push(password.clone()); // sensitive-guard:allow (ten flag / test sample)
    }
    let run = std::process::Command::new(&arguments[0])
        .args(&arguments[1..])
        .output();
    match run {
        Err(err) => ProvisionResult {
            resource,
            action: "failed".to_string(),
            message: err.to_string(),
            cause: Some(err.to_string()),
        },
        Ok(output) => {
            // std::process cannot enforce a timeout; long-running setups are
            // expected to be bounded by the helper script itself.
            let _ = timeout;
            if output.status.success() {
                ProvisionResult {
                    resource,
                    action: "created".to_string(),
                    message: "constraints and indexes ensured".to_string(),
                    cause: None,
                }
            } else {
                let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
                let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
                let message = if !stderr.is_empty() {
                    stderr
                } else if !stdout.is_empty() {
                    stdout
                } else {
                    format!("exit {}", output.status.code().unwrap_or(-1))
                };
                ProvisionResult {
                    resource,
                    action: "failed".to_string(),
                    message,
                    cause: None,
                }
            }
        }
    }
}

fn default_setup_script() -> Option<PathBuf> {
    let candidate = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../code-tiny/scripts/setup_constraints.py");
    if candidate.is_file() {
        Some(crate::util::resolve_path(&candidate))
    } else {
        None
    }
}

/// Tag map shared by lifecycle callers when rendering provision output
/// (`PROVISION_TAGS`).
pub fn provision_tag(action: &str) -> &'static str {
    match action {
        "created" => "[new]",
        "exists" => "[ok]",
        "skipped" => "[skip]",
        "failed" => "[fail]",
        _ => "[?]",
    }
}

/// Format a `ProvisionResult` for CLI output (`render_provision_line`).
pub fn render_provision_line(result: &ProvisionResult) -> String {
    format!(
        "{} {}: {}",
        provision_tag(&result.action),
        result.resource,
        result.message
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn endpoint_parsing() {
        assert_eq!(
            parse_falkordb_endpoint("redis://user:pass@localhost:6379").unwrap(), // sensitive-guard:allow (ten flag / test sample)
            (
                "localhost".to_string(),
                6379,
                false,
                Some(("user".to_string(), "pass".to_string())) // sensitive-guard:allow (ten flag / test sample)
            )
        );
        assert_eq!(
            parse_falkordb_endpoint("localhost:6380").unwrap(),
            ("localhost".to_string(), 6380, false, None)
        );
        assert!(parse_falkordb_endpoint("").is_err());
    }

    #[test]
    fn render_line() {
        let result = ProvisionResult {
            resource: "qdrant:code".to_string(),
            action: "created".to_string(),
            message: "ok".to_string(),
            cause: None,
        };
        assert_eq!(render_provision_line(&result), "[new] qdrant:code: ok");
    }
}

//! Journal-lane configuration port: `configure_journal_env` (shadow lanes)
//! plus the `cortex_harness.storage.targets` effective-graph-target
//! fingerprint used inside run metadata.
//!
//! Python-plane fallback: `required`/`shared-required` journal lanes (SQLite
//! store, resume/quarantine, finalize drain) are NOT ported — the
//! orchestrator delegates the whole run to the Python orchestrator when a
//! required lane would execute (see orchestrator::maybe_delegate_python).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use cortex_graph_core::schema_manifest::code_graph_schema;
use serde_json::json;

use crate::syncscope;
use crate::util;

pub const MODE_ENV: &str = "CORTEX_GRAPH_JOURNAL_MODE";
pub const PATH_ENV: &str = "CORTEX_GRAPH_JOURNAL_PATH";
pub const METADATA_ENV: &str = "CORTEX_GRAPH_JOURNAL_METADATA";
pub const GENERATION_ENV: &str = "CORTEX_GRAPH_JOURNAL_GENERATION";

pub const OFF_MODES: [&str; 5] = ["", "0", "false", "off", "disabled"];
pub const SHADOW_MODES: [&str; 3] = ["shadow", "shared-shadow", "cplus-canary"];
pub const REQUIRED_MODES: [&str; 2] = ["required", "shared-required"];

/// `_normalize_mode`.
pub fn normalize_mode(raw: &str) -> Result<String, String> {
    let value = raw.trim().to_lowercase();
    if OFF_MODES.contains(&value.as_str()) {
        return Ok("off".to_string());
    }
    if SHADOW_MODES.contains(&value.as_str()) {
        return Ok("shadow".to_string());
    }
    if REQUIRED_MODES.contains(&value.as_str()) {
        return Ok("required".to_string());
    }
    if value == "all-required" {
        return Err(
            "all-required rollout is not available until direct mutation inventory is complete"
                .to_string(),
        );
    }
    Err(format!("unsupported graph journal mode: {raw}"))
}

/// `_journal_mode_for_lane` — phase-03 (M2): default migrated lane cplus
/// chuyển `shared-required` → `off` (direct-write như 37 parser còn lại);
/// non-cplus giữ `shared-shadow`. Callers skip journal-env cho lane "off".
pub fn journal_mode_for_lane(env: &BTreeMap<String, String>, lane: &str) -> String {
    let configured = env
        .get("CORTEX_GRAPH_JOURNAL_MODE")
        .cloned()
        .unwrap_or_else(|| std::env::var("CORTEX_GRAPH_JOURNAL_MODE").unwrap_or_default())
        .trim()
        .to_string();
    if !configured.is_empty() {
        return configured;
    }
    if lane.eq_ignore_ascii_case("cplus") {
        "off".to_string()
    } else {
        "shared-shadow".to_string()
    }
}

// ── storage targets (cortex_harness/storage/targets.py port) ──────────────

const TARGET_SCHEMA_VERSION: i64 = 1;

const DEFAULT_PORTS: [(&str, u16); 11] = [
    ("http", 80),
    ("https", 443),
    ("redis", 6379),
    ("rediss", 6379),
    ("falkor", 6379),
    ("falkors", 6379),
    ("bolt", 7687),
    ("bolt+s", 7687),
    ("neo4j", 7687),
    ("neo4j+s", 7687),
    ("neo4j+ssc", 7687),
];

const TLS_SCHEMES: [&str; 6] = ["https", "rediss", "falkors", "bolt+s", "neo4j+s", "neo4j+ssc"];

fn canonical_local_target(path: &str) -> String {
    let expanded = if let Some(rest) = path.strip_prefix("~/") {
        std::env::var("HOME").map(|home| Path::new(&home).join(rest)).unwrap_or_else(|_| PathBuf::from(path))
    } else {
        PathBuf::from(path)
    };
    let resolved = util::realpath(&expanded.to_string_lossy());
    resolved.to_string_lossy().to_string()
}

/// `canonical_remote_endpoint` — normalize a URI and drop userinfo.
fn canonical_remote_endpoint(value: &str, default_scheme: &str) -> Result<(String, Option<String>), String> {
    let raw = value.trim();
    if raw.is_empty() {
        return Err("remote storage endpoint must not be empty".to_string());
    }
    let candidate = if raw.contains("://") {
        raw.to_string()
    } else {
        format!("{default_scheme}://{raw}")
    };
    let (scheme, rest) = candidate.split_once("://").ok_or("missing scheme")?;
    let scheme = scheme.to_lowercase();
    if scheme.is_empty() {
        return Err("remote storage endpoint has no scheme".to_string());
    }
    if scheme == "unix" {
        return Ok((
            format!("unix://{}", canonical_local_target(rest)),
            None,
        ));
    }
    // authority = up to the first '/', '?' or '#'.
    let authority_end = rest
        .find(['/', '?', '#'])
        .unwrap_or(rest.len());
    let authority = &rest[..authority_end];
    let path = &rest[authority_end..];
    // urlsplit: userinfo ends at the LAST '@' in the authority.
    let (userinfo, hostport) = match authority.rfind('@') {
        Some(index) => (Some(authority[..index].to_string()), &authority[index + 1..]),
        None => (None, authority),
    };
    let username = userinfo.as_ref().and_then(|info| info.split(':').next().map(str::to_string));
    // Strip port.
    let (host_raw, port_raw): (String, Option<String>) = if hostport.starts_with('[') {
        match hostport.find(']') {
            Some(end) => {
                let host = hostport[..=end].to_string();
                let remainder = &hostport[end + 1..];
                let port = remainder.strip_prefix(':').map(str::to_string);
                (host, port)
            }
            None => (hostport.to_string(), None),
        }
    } else {
        match hostport.rfind(':') {
            Some(index) => (
                hostport[..index].to_string(),
                Some(hostport[index + 1..].to_string()),
            ),
            None => (hostport.to_string(), None),
        }
    };
    if host_raw.is_empty() {
        return Err("remote storage endpoint has no host".to_string());
    }
    let mut host = host_raw.to_lowercase();
    while host.ends_with('.') {
        host.pop();
    }
    if host.contains(':') && !host.starts_with('[') {
        host = format!("[{host}]");
    }
    let port: Option<u16> = match port_raw {
        Some(raw) if !raw.is_empty() => match raw.parse() {
            Ok(port) => Some(port),
            Err(_) => return Err("remote storage endpoint has an invalid port".to_string()),
        },
        _ => None,
    };
    let port = port.or_else(|| {
        DEFAULT_PORTS
            .iter()
            .find(|(default_scheme, _)| *default_scheme == scheme)
            .map(|(_, default_port)| *default_port)
    });
    let netloc = match port {
        Some(port) => format!("{host}:{port}"),
        None => host,
    };
    let trimmed_path = path.trim_end_matches('/');
    Ok((format!("{scheme}://{netloc}{trimmed_path}"), username))
}

fn endpoint_uses_tls(endpoint: &str, explicit: bool) -> bool {
    let probe = if endpoint.contains("://") {
        endpoint.to_string()
    } else {
        format!("//{endpoint}")
    };
    let scheme = probe.split("://").next().unwrap_or("").to_lowercase();
    explicit || TLS_SCHEMES.contains(&scheme.as_str())
}

fn principal_fingerprint(value: Option<&str>) -> Option<String> {
    let normalized = value.unwrap_or_default().trim().to_lowercase();
    if normalized.is_empty() {
        return None;
    }
    Some(format!("sha256:{}", util::sha256_hex(normalized.as_bytes())))
}

/// `EffectiveStorageTarget`.
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct EffectiveStorageTarget {
    pub component: String,
    pub provider: String,
    pub mode: String,
    pub location: String,
    pub namespace: String,
    pub role: String,
    pub tls: bool,
    pub principal_fingerprint: Option<String>,
    pub schema_version: i64,
}

impl EffectiveStorageTarget {
    pub fn to_dict(&self) -> serde_json::Value {
        let mut map = serde_json::Map::new();
        map.insert("component".into(), json!(self.component));
        map.insert("location".into(), json!(self.location));
        map.insert("mode".into(), json!(self.mode));
        map.insert("namespace".into(), json!(self.namespace));
        map.insert("provider".into(), json!(self.provider));
        map.insert("role".into(), json!(self.role));
        map.insert("schema_version".into(), json!(self.schema_version));
        map.insert("tls".into(), json!(self.tls));
        if let Some(principal) = &self.principal_fingerprint {
            map.insert("principal_fingerprint".into(), json!(principal));
        }
        serde_json::Value::Object(map)
    }

    pub fn canonical_json(&self) -> String {
        util::canonical_json_string(&self.to_dict())
    }

    pub fn fingerprint(&self) -> String {
        format!(
            "storage-target:v{VERSION}:{}",
            util::sha256_hex(self.canonical_json().as_bytes()),
            VERSION = self.schema_version
        )
    }
}

fn role_value(role: &str) -> String {
    let normalized = role.trim().to_lowercase();
    let normalized = if normalized == "document" { "doc".to_string() } else { normalized };
    if normalized != "code" && normalized != "doc" {
        panic!("storage role must be 'code' or 'doc'; got {role:?}");
    }
    normalized
}

/// `remote_graph_target`.
pub fn remote_graph_target(
    uri: &str,
    graph: &str,
    role: &str,
    _password: Option<&str>, // sensitive-guard:allow (tên flag/biến, không phải secret)
    principal: Option<&str>,
    ssl: bool,
    provider: &str,
) -> Result<EffectiveStorageTarget, String> {
    let normalized_provider = provider.to_lowercase();
    if normalized_provider == "ladybug" {
        return Err("ladybug is local-only; use falkordb/neo4j for remote graph targets".to_string());
    }
    let default_scheme = if normalized_provider == "falkordb" { "redis" } else { "bolt" };
    let (location, uri_principal) = canonical_remote_endpoint(uri, default_scheme)?;
    let effective_principal = principal.map(str::to_string).or(uri_principal);
    let principal_fp = principal_fingerprint(effective_principal.as_deref());
    let tls = endpoint_uses_tls(&location, ssl);
    Ok(EffectiveStorageTarget {
        component: "graph".to_string(),
        provider: provider.to_string(),
        mode: "remote".to_string(),
        location,
        namespace: graph.to_string(),
        role: role_value(role),
        tls,
        principal_fingerprint: principal_fp,
        schema_version: TARGET_SCHEMA_VERSION,
    })
}

/// `local_graph_target`.
pub fn local_graph_target(path: &str, graph: &str, role: &str, provider: &str) -> EffectiveStorageTarget {
    EffectiveStorageTarget {
        component: "graph".to_string(),
        provider: provider.to_lowercase(),
        mode: "file".to_string(),
        location: canonical_local_target(path),
        namespace: graph.to_string(),
        role: role_value(role),
        tls: false,
        principal_fingerprint: None,
        schema_version: TARGET_SCHEMA_VERSION,
    }
}

/// `_runtime_graph_target_from_env` — reconstruct from live env values.
pub fn runtime_graph_target_from_env(env: &BTreeMap<String, String>) -> Result<EffectiveStorageTarget, String> {
    let lookup = |name: &str| -> Option<String> {
        env.get(name).cloned().or_else(|| std::env::var(name).ok()).filter(|v| !v.trim().is_empty())
    };
    let role = lookup("CORTEX_STORAGE_OWNER").unwrap_or_else(|| "code".to_string());
    let provider = lookup("CODE_GRAPH_PROVIDER")
        .or_else(|| lookup("GRAPH_PROVIDER"))
        .unwrap_or_else(|| "falkordb".to_string())
        .to_lowercase();
    if provider == "neo4j" || provider == "neo" {
        return remote_graph_target(
            &lookup("NEO4J_URI").unwrap_or_else(|| "bolt://localhost:7687".to_string()),
            &lookup("NEO4J_DB").unwrap_or_else(|| "neo4j".to_string()),
            &role,
            lookup("NEO4J_PASSWORD").as_deref(),
            lookup("NEO4J_USER").as_deref(),
            false,
            "neo4j",
        );
    }
    if provider == "ladybug" {
        if lookup("FALKORDB_URI").is_some() || lookup("NEO4J_URI").is_some() {
            return Err("ladybug is local-only; unset the remote endpoint or select the provider it belongs to".to_string());
        }
        return Ok(local_graph_target(
            &lookup("LADYBUG_PATH").unwrap_or_else(|| "embedded".to_string()),
            &lookup("LADYBUG_GRAPH").unwrap_or_else(|| "hyper_graph".to_string()),
            &role,
            "ladybug",
        ));
    }
    let graph = lookup("FALKORDB_GRAPH")
        .or_else(|| lookup("FALKORDB_DATABASE"))
        .unwrap_or_else(|| "hyper_graph".to_string());
    let uri = lookup("FALKORDB_URI").unwrap_or_default();
    if !uri.is_empty() {
        let ssl = !matches!(
            lookup("FALKORDB_SSL").unwrap_or_default().to_lowercase().as_str(),
            "" | "0" | "false" | "no" | "off"
        );
        return remote_graph_target(
            &uri,
            &graph,
            &role,
            lookup("FALKORDB_PASSWORD").as_deref(),
            None,
            ssl,
            "falkordb",
        );
    }
    Ok(local_graph_target(
        &lookup("FALKORDB_PATH").unwrap_or_else(|| "embedded".to_string()),
        &graph,
        &role,
        "falkordb",
    ))
}

/// `physical_target_from_env` — the effective graph-target fingerprint.
pub fn physical_target_from_env(env: &BTreeMap<String, String>) -> Result<String, String> {
    runtime_graph_target_from_env(env).map(|target| target.fingerprint())
}

// ── configure_journal_env ─────────────────────────────────────────────────

#[allow(dead_code)]
pub struct JournalEnvConfig {
    pub mode: String,
    pub path: PathBuf,
    pub generation: String,
    pub metadata_json: String,
}

/// `configure_journal_env` — shadow lanes only; required-lane bookkeeping
/// (quarantine/resume) is Python-plane.
#[allow(clippy::too_many_arguments)]
pub fn configure_journal_env(
    env: &mut BTreeMap<String, String>,
    root: &Path,
    project_id: &str,
    parser: &str,
    source_revision: &str,
    source_snapshot: &str,
    physical_target: &str,
    cache_dir: &Path,
    mode: &str,
    generation: Option<&str>,
) -> Result<JournalEnvConfig, String> {
    let normalized_mode = normalize_mode(mode)?;
    if normalized_mode == "off" {
        return Err("configure_journal_env requires an enabled mode".to_string());
    }
    let canonical_root = util::realpath(&util::path_to_string(root));
    let scope_id = syncscope::scan_scope_id(project_id, &canonical_root);
    let journal_root = cache_dir.join("graph-write-journal").join(&scope_id);
    let safe_parser: String = parser
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '_' })
        .collect();
    let path = journal_root.join(format!("{safe_parser}.sqlite3"));
    let run_generation = generation
        .map(str::to_string)
        .or_else(|| env.get(GENERATION_ENV).cloned())
        .unwrap_or_else(|| source_snapshot.to_string());
    let query_shape_version = if parser.to_lowercase() == "cplus" {
        "language-writer-node-first-v1"
    } else {
        "language-writer-v1"
    };
    let mut operation_versions = BTreeMap::new();
    operation_versions.insert("graph-write".to_string(), 1i64);
    if parser.to_lowercase() == "cplus" {
        operation_versions.insert("node-first".to_string(), 1i64);
    }
    let metadata = json!({
        "project_id": project_id,
        "scope_id": scope_id,
        "source_revision": if source_revision.is_empty() { source_snapshot } else { source_revision },
        "source_snapshot": source_snapshot,
        "physical_target": physical_target,
        "generation": run_generation,
        "parser": parser,
        "parser_version": "1",
        "schema_fingerprint": code_graph_schema().fingerprint(),
        "query_shape_version": query_shape_version,
        "operation_versions": operation_versions,
        "contract_version": 1,
    });
    let metadata_json = String::from_utf8(cortex_graph_core::identity::canonical_json(&metadata)).unwrap();
    env.insert(MODE_ENV.to_string(), normalized_mode.clone());
    env.insert(PATH_ENV.to_string(), path.to_string_lossy().to_string());
    env.insert(GENERATION_ENV.to_string(), run_generation.clone());
    env.insert(METADATA_ENV.to_string(), metadata_json.clone());
    Ok(JournalEnvConfig {
        mode: normalized_mode,
        path,
        generation: run_generation,
        metadata_json,
    })
}

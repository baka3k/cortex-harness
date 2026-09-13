//! Canonical, credential-free identities for effective storage targets.
//!
//! Port of `cortex_harness.storage.targets`. The backend selector is only an
//! operator request; journal and generation compatibility must instead bind
//! the targets that a run will actually mutate, including mixed-mode fallback
//! and the emergency force-local override.

use std::collections::BTreeMap;
use std::fmt;
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::contracts::CaseFoldExt;
use crate::errors::{StoreError, StoreResult};
use crate::util::{canonical_json, resolve_path};

pub const TARGET_SCHEMA_VERSION: i64 = 1;
pub const ENV_EFFECTIVE_GRAPH_TARGET: &str = "CORTEX_EFFECTIVE_GRAPH_TARGET";
pub const ENV_EFFECTIVE_GRAPH_FINGERPRINT: &str = "CORTEX_EFFECTIVE_GRAPH_TARGET_FINGERPRINT";
pub const ENV_EFFECTIVE_VECTOR_TARGET: &str = "CORTEX_EFFECTIVE_VECTOR_TARGET";
pub const ENV_EFFECTIVE_VECTOR_FINGERPRINT: &str = "CORTEX_EFFECTIVE_VECTOR_TARGET_FINGERPRINT";
pub const ENV_EFFECTIVE_TOPOLOGY: &str = "CORTEX_EFFECTIVE_STORAGE_TOPOLOGY";
pub const ENV_EFFECTIVE_TOPOLOGY_FINGERPRINT: &str =
    "CORTEX_EFFECTIVE_STORAGE_TOPOLOGY_FINGERPRINT";

fn default_port(scheme: &str) -> Option<u16> {
    match scheme {
        "http" => Some(80),
        "https" => Some(443),
        "redis" | "rediss" | "falkor" | "falkors" => Some(6379),
        "bolt" | "bolt+s" | "neo4j" | "neo4j+s" | "neo4j+ssc" => Some(7687),
        _ => None,
    }
}

fn is_tls_scheme(scheme: &str) -> bool {
    matches!(
        scheme,
        "https" | "rediss" | "falkors" | "bolt+s" | "neo4j+s" | "neo4j+ssc"
    )
}

fn sha256_hex(value: &str) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(value.as_bytes());
    format!("{:x}", hasher.finalize())
}

/// Fingerprint a non-secret principal name, never credential material.
pub fn principal_fingerprint_from(value: Option<&str>) -> Option<String> {
    principal_fingerprint(value)
}

/// Fingerprint a non-secret principal name, never credential material.
pub fn principal_fingerprint(value: Option<&str>) -> Option<String> {
    let normalized = value.unwrap_or("").trim().to_casefold();
    if normalized.is_empty() {
        None
    } else {
        Some(format!("sha256:{}", sha256_hex(&normalized)))
    }
}

/// Parse an opt-in environment flag without treating `0` as enabled.
pub fn environment_flag_enabled(value: Option<&str>) -> bool {
    matches!(
        value.unwrap_or("").trim().to_casefold().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// Normalize a storage role to `code` or `doc` (Python `_role_value`).
pub fn role_value(role: impl Into<RoleInput>) -> StoreResult<String> {
    let raw = match role.into() {
        RoleInput::Str(text) => text,
        RoleInput::Code => "code".to_string(),
        RoleInput::Doc => "doc".to_string(),
    };
    let mut normalized = raw.trim().to_casefold();
    if normalized == "document" {
        normalized = "doc".to_string();
    }
    if normalized != "code" && normalized != "doc" {
        return Err(StoreError::Value(format!(
            "storage role must be 'code' or 'doc'; got {raw:?}"
        )));
    }
    Ok(normalized)
}

/// Input wrapper so callers can pass &str or the typed role enums. // sensitive-guard:allow (ten flag / test sample)
#[derive(Debug, Clone)]
pub enum RoleInput {
    Str(String),
    Code,
    Doc,
}

impl From<&str> for RoleInput {
    fn from(value: &str) -> Self {
        RoleInput::Str(value.to_string())
    }
}

impl From<String> for RoleInput {
    fn from(value: String) -> Self {
        RoleInput::Str(value)
    }
}

/// Return an absolute canonical path without requiring it to exist.
pub fn canonical_local_target(path: impl AsRef<Path>) -> String {
    resolve_path(path.as_ref()).to_string_lossy().into_owned()
}

/// Return only a syntactically valid scheme for redacted diagnostics.
fn safe_endpoint_scheme_hint(candidate: &str, default_scheme: &str) -> String {
    let hint = if candidate.contains("://") {
        candidate.split("://").next().unwrap_or("").to_string()
    } else {
        default_scheme.to_string()
    };
    let mut chars = hint.chars();
    let Some(first) = chars.next() else {
        return "<invalid-scheme>".to_string();
    };
    if !first.is_ascii_alphabetic() {
        return "<invalid-scheme>".to_string();
    }
    for character in chars {
        if !character.is_ascii_alphanumeric() && !matches!(character, '+' | '-' | '.') {
            return "<invalid-scheme>".to_string();
        }
    }
    hint.to_casefold()
}

/// A minimal urlsplit equivalent covering the endpoint grammar used here,
/// reproducing Python `urlsplit` semantics for scheme, userinfo, hostname
/// (lowercased), port (strict digits, 0-65535), and path.
struct SplitUrl {
    scheme: String,
    #[allow(dead_code)]
    netloc: String,
    hostname: Option<String>,
    port: Option<Result<Option<u16>, ()>>,
    username: Option<String>,
    path: String,
}

fn split_url(candidate: &str) -> Result<SplitUrl, ()> {
    // Scheme must be [A-Za-z][A-Za-z0-9+-.]* followed by ':'.
    let Some(colon) = candidate.find(':') else {
        return Err(());
    };
    let scheme_raw = &candidate[..colon];
    let mut chars = scheme_raw.chars();
    let valid = matches!(chars.next(), Some(first) if first.is_ascii_alphabetic())
        && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.'));
    if !valid {
        return Err(());
    }
    let rest = &candidate[colon + 1..];
    let (netloc, tail) = if let Some(after) = rest.strip_prefix("//") {
        let end = after
            .find(['/', '?', '#'])
            .unwrap_or(after.len());
        (after[..end].to_string(), after[end..].to_string())
    } else {
        (String::new(), rest.to_string())
    };
    // urlsplit post-check: bracket characters must be balanced.
    if netloc.contains('[') != netloc.contains(']') {
        return Err(());
    }
    // Path ends at '?' or '#'.
    let path_end = tail.find(['?', '#']).unwrap_or(tail.len());
    let path = tail[..path_end].to_string();

    // hostinfo: userinfo before the last '@'.
    let (userinfo, hostinfo) = match netloc.rfind('@') {
        Some(idx) => (Some(netloc[..idx].to_string()), netloc[idx + 1..].to_string()),
        None => (None, netloc.clone()),
    };
    let username = userinfo.map(|info| info.split(':').next().unwrap_or("").to_string());

    // hostname/port per Python `_hostinfo`.
    let (hostname_raw, port_raw) = if hostinfo.contains('[') {
        let open = hostinfo.find('[').unwrap();
        let bracketed = &hostinfo[open + 1..];
        match bracketed.find(']') {
            Some(close) => {
                let host = bracketed[..close].to_string();
                let port_part = &bracketed[close + 1..];
                let port = port_part.strip_prefix(':').map(str::to_string);
                (host, port)
            }
            None => {
                return Err(()); // urlsplit raised Invalid IPv6 URL earlier anyway
            }
        }
    } else {
        match hostinfo.split_once(':') {
            Some((host, port)) => (host.to_string(), Some(port.to_string())),
            None => (hostinfo.clone(), None),
        }
    };
    let hostname = if hostname_raw.is_empty() {
        None
    } else {
        let lowered = hostname_raw.to_casefold();
        if hostinfo.contains('[') && !valid_bracketed_host(&lowered) {
            return Err(());
        }
        Some(lowered)
    };

    Ok(SplitUrl {
        scheme: scheme_raw.to_casefold(),
        netloc,
        hostname,
        port: Some(parse_port(port_raw.as_deref())),
        username,
        path,
    })
}

fn valid_bracketed_host(host: &str) -> bool {
    // Python: ipaddress.ip_address(...) or the 'v' + hex + '.' zone escape.
    if host.parse::<std::net::IpAddr>().is_ok() {
        return true;
    }
    if let Some(rest) = host.strip_prefix('v') {
        let mut chars = rest.chars();
        if matches!(chars.next(), Some(c) if c.is_ascii_hexdigit()) {
            let mut split_ok = false;
            for c in chars {
                if c == '.' {
                    split_ok = true;
                    break;
                }
                if !c.is_ascii_hexdigit() {
                    return false;
                }
            }
            return split_ok;
        }
    }
    false
}

fn parse_port(raw: Option<&str>) -> Result<Option<u16>, ()> {
    let Some(raw) = raw else {
        return Ok(None);
    };
    if raw.is_empty() {
        return Ok(None);
    }
    // Python 3.11+ requires strict digits for the port substring.
    if !raw.chars().all(|c| c.is_ascii_digit()) || raw.len() > 19 {
        return Err(());
    }
    match raw.parse::<u64>() {
        Ok(value) if value <= 65535 => Ok(Some(value as u16)),
        _ => Err(()),
    }
}

/// Normalize an endpoint and remove userinfo, query strings, and fragments.
///
/// The returned principal is suitable only as input to a one-way tenancy
/// fingerprint; it is never included in the canonical endpoint itself.
pub fn canonical_remote_endpoint(
    value: &str,
    default_scheme: &str,
) -> StoreResult<(String, Option<String>)> {
    let raw = value.trim();
    if raw.is_empty() {
        return Err(StoreError::Value(
            "remote storage endpoint must not be empty".to_string(),
        ));
    }
    let candidate = if raw.contains("://") {
        raw.to_string()
    } else {
        format!("{default_scheme}://{raw}")
    };
    let scheme_hint = safe_endpoint_scheme_hint(&candidate, default_scheme);
    let parsed = split_url(&candidate).map_err(|()| {
        StoreError::Value(format!(
            "remote storage endpoint is malformed (redacted endpoint: \
             '{scheme_hint}://<invalid-host>')"
        ))
    })?;
    let scheme = parsed.scheme.clone();
    if scheme.is_empty() {
        return Err(StoreError::Value(
            "remote storage endpoint has no scheme (redacted endpoint: \
             '<missing-scheme>://<redacted>')"
                .to_string(),
        ));
    }
    if scheme == "unix" {
        if parsed.path.is_empty() {
            return Err(StoreError::Value(
                "unix storage endpoint requires an absolute socket path".to_string(),
            ));
        }
        return Ok((
            format!("unix:{}", canonical_local_target(&parsed.path)),
            parsed.username,
        ));
    }
    let Some(hostname) = parsed.hostname.clone() else {
        let diagnostic = format!("{scheme}://<missing-host>");
        return Err(StoreError::Value(format!(
            "remote storage endpoint has no host (redacted endpoint: {diagnostic:?})"
        )));
    };
    let mut host = hostname.trim_end_matches('.').to_string();
    if host.contains(':') && !host.starts_with('[') {
        host = format!("[{host}]");
    }
    let port = match parsed.port {
        Some(Ok(port)) => port,
        Some(Err(())) => {
            let diagnostic = format!("{scheme}://{host}:<invalid-port>");
            return Err(StoreError::Value(format!(
                "remote storage endpoint has an invalid port (redacted endpoint: {diagnostic:?})"
            )));
        }
        None => None,
    };
    let port = port.or_else(|| default_port(&scheme));
    let netloc = match port {
        Some(port) => format!("{host}:{port}"),
        None => host,
    };
    let path = parsed.path.trim_end_matches('/');
    let canonical = format!("{scheme}://{netloc}{path}");
    Ok((canonical, parsed.username))
}

/// Whether an endpoint (or its explicit override) selects TLS.
pub fn endpoint_uses_tls(endpoint: &str, explicit: bool) -> bool {
    let scheme = match endpoint.split_once("://") {
        Some((scheme, _)) => scheme.to_casefold(),
        // `urlsplit("//host:port")` yields an empty scheme.
        None => String::new(),
    };
    explicit || is_tls_scheme(&scheme)
}

/// One effective graph or vector materialization target.
#[derive(Debug, Clone, PartialEq)]
pub struct EffectiveStorageTarget {
    pub component: String,
    pub provider: String,
    pub mode: String,
    pub location: String,
    pub namespace: String,
    pub role: String,
    pub tls: bool,
    pub principal_fingerprint: Option<String>,
    pub capability_fingerprint: Option<String>,
    pub schema_fingerprint: Option<String>,
    pub schema_version: i64,
}

impl EffectiveStorageTarget {
    /// Validate + normalize exactly like the Python `__post_init__`.
    #[allow(clippy::too_many_arguments)]
    pub fn create(
        component: &str,
        provider: &str,
        mode: &str,
        location: &str,
        namespace: &str,
        role: &str,
        tls: bool,
        principal_fingerprint: Option<String>,
        capability_fingerprint: Option<String>,
        schema_fingerprint: Option<String>,
        schema_version: i64,
    ) -> StoreResult<Self> {
        let component = component.trim().to_casefold();
        let mode_value = mode.trim().to_casefold();
        let provider = provider.trim().to_casefold();
        let namespace = namespace.trim().to_string();
        if component != "graph" && component != "vector" {
            return Err(StoreError::Value(
                "storage target component must be graph or vector".to_string(),
            ));
        }
        if mode_value != "file" && mode_value != "remote" {
            return Err(StoreError::Value(
                "storage target mode must be file or remote".to_string(),
            ));
        }
        if provider.is_empty() || location.trim().is_empty() || namespace.is_empty() {
            return Err(StoreError::Value(
                "storage target provider, location, and namespace must not be empty".to_string(),
            ));
        }
        if schema_version != TARGET_SCHEMA_VERSION {
            return Err(StoreError::Value(format!(
                "unsupported effective storage target schema version: {schema_version:?}"
            )));
        }
        let role = role_value(role)?;
        let (location, tls, principal) = if mode_value == "file" {
            let location = canonical_local_target(location);
            if tls || principal_fingerprint.is_some() {
                return Err(StoreError::Value(
                    "file storage targets cannot declare TLS or a remote principal".to_string(),
                ));
            }
            (location, false, None)
        } else {
            let default_scheme = if component == "vector" {
                "http"
            } else if provider == "neo" || provider == "neo4j" {
                "bolt"
            } else {
                "redis"
            };
            let (location, uri_principal) =
                canonical_remote_endpoint(location, default_scheme)?;
            let principal = match principal_fingerprint.clone() {
                Some(existing) => Some(existing),
                None => super::targets::principal_fingerprint_from(uri_principal.as_deref()),
            };
            let tls = endpoint_uses_tls(&location, tls);
            (location, tls, principal)
        };
        if let Some(principal_value) = &principal {
            let valid = principal_value.starts_with("sha256:")
                && principal_value.len() == "sha256:".len() + 64
                && principal_value["sha256:".len()..]
                    .chars()
                    .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase());
            if !valid {
                return Err(StoreError::Value(
                    "storage principal fingerprint must be a SHA-256 digest".to_string(),
                ));
            }
        }
        Ok(Self {
            component,
            provider,
            mode: mode_value,
            location,
            namespace,
            role,
            tls,
            principal_fingerprint: principal,
            capability_fingerprint,
            schema_fingerprint,
            schema_version,
        })
    }

    /// Dict with `None` fields dropped (`to_dict`).
    pub fn to_value(&self) -> Value {
        let mut out = Map::new();
        out.insert("component".to_string(), Value::from(self.component.as_str()));
        out.insert("provider".to_string(), Value::from(self.provider.as_str()));
        out.insert("mode".to_string(), Value::from(self.mode.as_str()));
        out.insert("location".to_string(), Value::from(self.location.as_str()));
        out.insert("namespace".to_string(), Value::from(self.namespace.as_str()));
        out.insert("role".to_string(), Value::from(self.role.as_str()));
        out.insert("tls".to_string(), Value::from(self.tls));
        if let Some(value) = &self.principal_fingerprint {
            out.insert("principal_fingerprint".to_string(), Value::from(value.as_str()));
        }
        if let Some(value) = &self.capability_fingerprint {
            out.insert("capability_fingerprint".to_string(), Value::from(value.as_str()));
        }
        if let Some(value) = &self.schema_fingerprint {
            out.insert("schema_fingerprint".to_string(), Value::from(value.as_str()));
        }
        out.insert(
            "schema_version".to_string(),
            Value::from(self.schema_version),
        );
        Value::Object(out)
    }

    pub fn from_value(value: &Value) -> StoreResult<Self> {
        let map = value
            .as_object()
            .ok_or_else(|| StoreError::Value("effective storage target must be a JSON object".to_string()))?;
        let required = ["component", "provider", "mode", "location", "namespace", "role"];
        let mut fields: BTreeMap<String, Value> = BTreeMap::new();
        for key in required {
            let field = map.get(key).ok_or_else(|| {
                StoreError::Value(format!("effective storage target is missing {key}"))
            })?;
            fields.insert(key.to_string(), field.clone());
        }
        for (key, value) in map {
            if key == "tls"
                || key == "principal_fingerprint"
                || key == "capability_fingerprint"
                || key == "schema_fingerprint"
                || key == "schema_version"
                || required.contains(&key.as_str())
            {
                fields.insert(key.clone(), value.clone());
            } else {
                return Err(StoreError::Value(format!(
                    "effective storage target has an unexpected field: {key}"
                )));
            }
        }
        let text = |key: &str| -> String {
            fields
                .get(key)
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string()
        };
        Self::create(
            &text("component"),
            &text("provider"),
            &text("mode"),
            &text("location"),
            &text("namespace"),
            &text("role"),
            fields.get("tls").and_then(Value::as_bool).unwrap_or(false),
            fields
                .get("principal_fingerprint")
                .and_then(Value::as_str)
                .map(str::to_string),
            fields
                .get("capability_fingerprint")
                .and_then(Value::as_str)
                .map(str::to_string),
            fields
                .get("schema_fingerprint")
                .and_then(Value::as_str)
                .map(str::to_string),
            fields
                .get("schema_version")
                .and_then(Value::as_i64)
                .unwrap_or(TARGET_SCHEMA_VERSION),
        )
    }

    pub fn from_json(value: &str) -> StoreResult<Self> {
        let payload: Value = serde_json::from_str(value)
            .map_err(|err| StoreError::Value(format!("invalid effective storage target JSON: {err}")))?;
        Self::from_value(&payload)
    }

    pub fn canonical_json(&self) -> String {
        canonical_json(&self.to_value())
    }

    pub fn fingerprint(&self) -> String {
        format!(
            "storage-target:v{}:{}",
            self.schema_version,
            sha256_hex(&self.canonical_json())
        )
    }
}

impl fmt::Display for EffectiveStorageTarget {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.canonical_json())
    }
}

/// The complete graph/vector topology resolved before run creation.
#[derive(Debug, Clone, PartialEq)]
pub struct EffectiveStorageTopology {
    pub project_scope: String,
    pub requested_backend: String,
    pub forced_local: bool,
    pub graph: EffectiveStorageTarget,
    pub vector: EffectiveStorageTarget,
    pub generation_id: String,
    pub schema_version: i64,
}

impl EffectiveStorageTopology {
    pub fn create(
        project_scope: &str,
        requested_backend: &str,
        forced_local: bool,
        generation_id: &str,
        graph: EffectiveStorageTarget,
        vector: EffectiveStorageTarget,
    ) -> StoreResult<Self> {
        let project_scope = project_scope.trim().to_string();
        let requested_backend = requested_backend.trim().to_casefold();
        let generation_id = generation_id.trim().to_string();
        if project_scope.is_empty() {
            return Err(StoreError::Value(
                "effective storage topology requires a project scope".to_string(),
            ));
        }
        if requested_backend != "local" && requested_backend != "remote" {
            return Err(StoreError::Value(
                "requested storage backend must be local or remote".to_string(),
            ));
        }
        if generation_id.is_empty() {
            return Err(StoreError::Value(
                "effective storage topology requires a generation ID".to_string(),
            ));
        }
        if schema_version_check() != TARGET_SCHEMA_VERSION {
            return Err(StoreError::Value(format!(
                "unsupported effective storage topology schema version: {}",
                TARGET_SCHEMA_VERSION
            )));
        }
        if graph.component != "graph" || vector.component != "vector" {
            return Err(StoreError::Value(
                "effective storage topology components are reversed or invalid".to_string(),
            ));
        }
        if graph.role != vector.role {
            return Err(StoreError::Value(
                "effective graph and vector targets must have the same owner role".to_string(),
            ));
        }
        if requested_backend == "local" && (graph.mode != "file" || vector.mode != "file") {
            return Err(StoreError::Value(
                "a local storage request cannot resolve to a remote target".to_string(),
            ));
        }
        if forced_local && (graph.mode != "file" || vector.mode != "file") {
            return Err(StoreError::Value(
                "a force-local topology cannot contain a remote target".to_string(),
            ));
        }
        Ok(Self {
            project_scope,
            requested_backend,
            forced_local,
            graph,
            vector,
            generation_id,
            schema_version: TARGET_SCHEMA_VERSION,
        })
    }

    pub fn to_value(&self) -> Value {
        json!({
            "schema_version": self.schema_version,
            "project_scope": self.project_scope,
            "requested_backend": self.requested_backend,
            "forced_local": self.forced_local,
            "generation_id": self.generation_id,
            "graph": self.graph.to_value(),
            "vector": self.vector.to_value(),
        })
    }

    pub fn canonical_json(&self) -> String {
        canonical_json(&self.to_value())
    }

    pub fn fingerprint(&self) -> String {
        format!(
            "storage-topology:v{}:{}",
            self.schema_version,
            sha256_hex(&self.canonical_json())
        )
    }

    pub fn graph_fingerprint(&self) -> String {
        self.graph.fingerprint()
    }

    pub fn vector_fingerprint(&self) -> String {
        self.vector.fingerprint()
    }

    /// Bind this resolved component pair to one staged generation.
    pub fn for_generation(&self, generation_id: &str) -> StoreResult<Self> {
        let value = generation_id.trim();
        if value.is_empty() {
            return Err(StoreError::Value(
                "effective storage topology requires a generation ID".to_string(),
            ));
        }
        let mut next = self.clone();
        next.generation_id = value.to_string();
        Ok(next)
    }

    /// Manifest-safe compatibility payload containing all target fences.
    pub fn compatibility_metadata(&self) -> Map<String, Value> {
        let mut out = Map::new();
        out.insert("schema_version".to_string(), json!(self.schema_version));
        out.insert("generation_id".to_string(), json!(self.generation_id));
        out.insert("graph_mode".to_string(), json!(self.graph.mode));
        out.insert("vector_mode".to_string(), json!(self.vector.mode));
        out.insert(
            "graph_target_fingerprint".to_string(),
            json!(self.graph_fingerprint()),
        );
        out.insert(
            "vector_target_fingerprint".to_string(),
            json!(self.vector_fingerprint()),
        );
        out.insert(
            "topology_fingerprint".to_string(),
            json!(self.fingerprint()),
        );
        out
    }
}

fn schema_version_check() -> i64 {
    TARGET_SCHEMA_VERSION
}

/// Build a remote graph target (`remote_graph_target`).
///
/// `password` is accepted for signature parity with the Python helper but is // sensitive-guard:allow (ten flag / test sample)
/// never materialized in a canonical target (credential-free identity).
#[allow(clippy::too_many_arguments)]
pub fn remote_graph_target(
    uri: &str,
    graph: &str,
    role: impl Into<RoleInput>,
    _password: Option<&str>, // sensitive-guard:allow (ten flag / test sample)
    principal: Option<&str>,
    ssl: bool,
    provider: &str,
) -> StoreResult<EffectiveStorageTarget> {
    let normalized_provider = provider.to_casefold();
    if normalized_provider == "ladybug" {
        return Err(StoreError::Value(
            "ladybug is local-only; use falkordb/neo4j for remote graph targets".to_string(),
        ));
    }
    let default_scheme = if normalized_provider == "falkordb" {
        "redis"
    } else {
        "bolt"
    };
    let (location, uri_principal) = canonical_remote_endpoint(uri, default_scheme)?;
    EffectiveStorageTarget::create(
        "graph",
        provider,
        "remote",
        &location,
        graph,
        &role_value(role)?,
        endpoint_uses_tls(&location, ssl),
        principal_fingerprint(principal.or(uri_principal.as_deref())),
        None,
        None,
        TARGET_SCHEMA_VERSION,
    )
}

/// Build a local file graph target (`local_graph_target`).
pub fn local_graph_target(
    path: impl AsRef<Path>,
    graph: &str,
    role: impl Into<RoleInput>,
    provider: &str,
) -> StoreResult<EffectiveStorageTarget> {
    EffectiveStorageTarget::create(
        "graph",
        &provider.to_casefold(),
        "file",
        &canonical_local_target(path.as_ref()),
        graph,
        &role_value(role)?,
        false,
        None,
        None,
        None,
        TARGET_SCHEMA_VERSION,
    )
}

/// Build a remote Qdrant target (`remote_vector_target`).
pub fn remote_vector_target(
    url: &str,
    collection: &str,
    role: impl Into<RoleInput>,
    api_key: Option<&str>,
) -> StoreResult<EffectiveStorageTarget> {
    let _ = api_key;
    let (location, principal) = canonical_remote_endpoint(url, "http")?;
    EffectiveStorageTarget::create(
        "vector",
        "qdrant",
        "remote",
        &location,
        collection,
        &role_value(role)?,
        endpoint_uses_tls(&location, false),
        principal_fingerprint(principal.as_deref()),
        None,
        None,
        TARGET_SCHEMA_VERSION,
    )
}

/// Build a local Qdrant target (`local_vector_target`).
pub fn local_vector_target(
    path: impl AsRef<Path>,
    collection: &str,
    role: impl Into<RoleInput>,
) -> StoreResult<EffectiveStorageTarget> {
    EffectiveStorageTarget::create(
        "vector",
        "qdrant",
        "file",
        &canonical_local_target(path.as_ref()),
        collection,
        &role_value(role)?,
        false,
        None,
        None,
        None,
        TARGET_SCHEMA_VERSION,
    )
}

/// Runtime graph target reconstruction from a propagated environment
/// (`_runtime_graph_target_from_env`).
fn runtime_graph_target_from_env(env: &BTreeMap<String, String>) -> StoreResult<EffectiveStorageTarget> {
    let get = |key: &str| env.get(key).map(String::as_str).unwrap_or("");
    let role = if get("CORTEX_STORAGE_OWNER").is_empty() {
        "code".to_string()
    } else {
        get("CORTEX_STORAGE_OWNER").to_string()
    };
    let mut provider = get("CODE_GRAPH_PROVIDER")
        .to_casefold();
    if provider.is_empty() {
        provider = get("GRAPH_PROVIDER").to_casefold();
    }
    if provider.is_empty() {
        provider = "falkordb".to_string();
    }
    if provider == "neo4j" || provider == "neo" {
        let uri = if get("NEO4J_URI").is_empty() {
            "bolt://localhost:7687"
        } else {
            get("NEO4J_URI")
        };
        let graph = if get("NEO4J_DB").is_empty() {
            "neo4j"
        } else {
            get("NEO4J_DB")
        };
        return remote_graph_target(
            uri,
            graph,
            RoleInput::Str(role),
            Some(get("NEO4J_PASSWORD")), // sensitive-guard:allow (ten flag / test sample)
            Some(get("NEO4J_USER")),
            false,
            "neo4j",
        );
    }
    if provider == "ladybug" {
        let graph = if get("LADYBUG_GRAPH").is_empty() {
            "hyper_graph"
        } else {
            get("LADYBUG_GRAPH")
        };
        // Ladybug is embedded-only: a remote endpoint is a configuration
        // error, never a fallback.
        if !get("FALKORDB_URI").trim().is_empty() || !get("NEO4J_URI").trim().is_empty() {
            return Err(StoreError::Value(
                "ladybug is local-only; unset the remote endpoint or select the provider it \
                 belongs to"
                    .to_string(),
            ));
        }
        let path = if get("LADYBUG_PATH").is_empty() {
            "embedded".to_string()
        } else {
            get("LADYBUG_PATH").to_string()
        };
        return local_graph_target(PathBuf::from(path), graph, RoleInput::Str(role), "ladybug");
    }
    let graph = if get("FALKORDB_GRAPH").is_empty() {
        if get("FALKORDB_DATABASE").is_empty() {
            "hyper_graph"
        } else {
            get("FALKORDB_DATABASE")
        }
    } else {
        get("FALKORDB_GRAPH")
    };
    let uri = get("FALKORDB_URI").trim();
    if !uri.is_empty() {
        let ssl_value = get("FALKORDB_SSL").trim().to_casefold();
        let ssl = !matches!(ssl_value.as_str(), "" | "0" | "false" | "no" | "off");
        return remote_graph_target(
            uri,
            graph,
            RoleInput::Str(role),
            Some(get("FALKORDB_PASSWORD")), // sensitive-guard:allow (ten flag / test sample)
            None,
            ssl,
            "falkordb",
        );
    }
    let path = if get("FALKORDB_PATH").is_empty() {
        "embedded".to_string()
    } else {
        get("FALKORDB_PATH").to_string()
    };
    local_graph_target(PathBuf::from(path), graph, RoleInput::Str(role), "falkordb")
}

/// Resolve and validate the graph target against the live runtime env
/// (`effective_graph_target_from_env`).
pub fn effective_graph_target_from_env(
    env: &BTreeMap<String, String>,
) -> StoreResult<EffectiveStorageTarget> {
    let descriptor = env
        .get(ENV_EFFECTIVE_GRAPH_TARGET)
        .map(String::as_str)
        .unwrap_or("")
        .trim();
    let supplied = if descriptor.is_empty() {
        None
    } else {
        Some(EffectiveStorageTarget::from_json(descriptor)?)
    };
    let runtime = runtime_graph_target_from_env(env)?;
    if let Some(supplied) = supplied {
        if supplied.component != "graph" {
            return Err(StoreError::Value(
                "effective graph descriptor has the wrong component".to_string(),
            ));
        }
        if supplied != runtime {
            return Err(StoreError::Value(
                "effective graph target descriptor does not match runtime \
                 provider/URI/path/graph/role"
                    .to_string(),
            ));
        }
        return Ok(supplied);
    }
    Ok(runtime)
}

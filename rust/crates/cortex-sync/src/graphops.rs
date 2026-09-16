//! Graph-plane helpers: `prepare_graph_args`, driver-style connection over
//! `cortex-falkordb`/`cortex-graph-writer::LadybugStore`, `ensure_schema` +
//! Project/Repository setup, the impact expansion queries, and the topology
//! bootstrap probe.

use std::collections::{BTreeMap, BTreeSet};

use cortex_graph_writer::store::{FalkorDbStore, GraphStore, LadybugStore};
use serde_json::json;

use crate::cli::Args;

#[allow(dead_code)]
pub struct GraphContext {
    pub provider: String,
    pub falkordb_uri: Option<String>,
    pub falkordb_path: Option<String>,
    /// Ladybug store file (resolved embedded target).
    pub ladybug_path: Option<String>,
    /// Resolved graph name — falkordb graph / ladybug named graph / neo4j db.
    pub falkordb_graph: String,
    pub neo4j_db: String,
}

fn env_lookup(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|v| !v.trim().is_empty())
}

pub fn graph_writes_disabled() -> bool {
    matches!(
        env_lookup("CORTEX_DISABLE_GRAPH").unwrap_or_default().trim().to_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// `parse_falkordb_uri` — scheme-stripped host:port.
pub fn parse_falkordb_uri(uri: &str) -> (String, u16) {
    let cleaned = uri
        .split("://")
        .last()
        .unwrap_or(uri)
        .trim_end_matches('/')
        .to_string();
    match cleaned.rsplit_once(':') {
        Some((host, port)) if !port.is_empty() && port.chars().all(|c| c.is_ascii_digit()) => {
            (host.to_string(), port.parse().unwrap_or(6379))
        }
        _ => (cleaned, 6379),
    }
}

/// ProjectRegistry default fill (port of `tools.common.project_registry.
/// resolve_project_targets` + `tools.graph.cli.apply_project_registry_defaults`).
///
/// Only `falkordb_graph` is filled — cortex-sync has no `--qdrant-collection`
/// flag (the qdrant half of the Python fill has no Rust consumer here), and
/// **ladybug_graph deliberately does NOT participate** (cli.py:249-256 chain
/// never consults the registry for ladybug; plan rev2 H1).
///
/// Config discovery anchors at `--root` walking up (mirror of cli.py's
/// `_resolve_config_dir`), NOT `Path.cwd()` like `project_registry`'s own
/// default — the orchestrator is root-addressable (parity/CI runs it from
/// any cwd). Unregistered project / missing dir → warning + fallback, never
/// a hard error (mirrors cli.py's `except ProjectNotRegisteredError`).
fn apply_project_registry_defaults(args: &mut Args) {
    let Some(project_id) = args
        .project_id
        .clone()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
    else {
        return;
    };
    let Some(config_dir) = discover_config_dir(&args.root) else {
        return;
    };
    let Some(registered_graph) = registry_falkordb_graph(&config_dir, &project_id) else {
        eprintln!(
            "[graph-cli] project_id {:?} not registered in discovered config; falling back to args.project_id for falkordb-graph.",
            args.project_id
        );
        return;
    };
    let explicit = args
        .falkordb_graph
        .as_deref()
        .map(str::trim)
        .filter(|g| !g.is_empty());
    if explicit.is_none() {
        args.falkordb_graph = Some(registered_graph);
    }
}

/// `.cortext-harness/config` discovery — walk up from `root` (inclusive).
fn discover_config_dir(root: &str) -> Option<std::path::PathBuf> {
    let mut current = Some(std::path::PathBuf::from(root));
    while let Some(dir) = current {
        let candidate = dir.join(".cortext-harness").join("config");
        if candidate.is_dir() {
            return Some(candidate);
        }
        current = dir.parent().map(std::path::Path::to_path_buf);
    }
    None
}

/// `FALKORDB_GRAPH` of the registered project (case-insensitive id match),
/// reading `*.json` sorted like `_read_config_files`.
fn registry_falkordb_graph(config_dir: &std::path::Path, project_id: &str) -> Option<String> {
    let lookup = project_id.trim().to_lowercase();
    let mut files: Vec<std::path::PathBuf> = std::fs::read_dir(config_dir)
        .ok()?
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.extension().map(|ext| ext == "json").unwrap_or(false))
        .collect();
    files.sort();
    for file in files {
        let Ok(text) = std::fs::read_to_string(&file) else {
            continue;
        };
        let Ok(payload) = serde_json::from_str::<serde_json::Value>(&text) else {
            continue;
        };
        let project = payload.get("project")?;
        let registered = project
            .get("code")
            .or_else(|| project.get("name"))
            .and_then(serde_json::Value::as_str)
            .map(|value| value.trim().to_lowercase());
        if registered.as_deref() != Some(lookup.as_str()) {
            continue;
        }
        let graph = payload
            .get("code")
            .and_then(|code| code.get("env"))
            .and_then(|env| env.get("FALKORDB_GRAPH"))
            .and_then(serde_json::Value::as_str)
            .map(str::to_string)
            .filter(|value| !value.trim().is_empty());
        return graph;
    }
    None
}

/// Resolved embedded store path via `cortex_storage::resolve_storage`.
///
/// Deliberate deviation from cli.py:243-244: Python resolves with
/// `Path.cwd()` (probe phase-01.4 showed a cwd≠root run lands in the WRONG
/// instance); the Rust orchestrator anchors at `--root`.
fn resolve_embedded_path(args: &Args, role: &str) -> Result<std::path::PathBuf, String> {
    let root = std::path::PathBuf::from(&args.root);
    let resolved = cortex_storage::resolve_storage(&root, None, &Default::default())
        .map_err(|error| format!("embedded storage resolution ({role}, anchor = --root): {error}"))?;
    resolved
        .ladybug_path_for_role(role)
        .map_err(|error| format!("embedded storage resolution ({role}, anchor = --root): {error}"))
}

/// `prepare_graph_args` — resolve the effective graph target. Returns None
/// when graph writes are disabled; Err is fail-closed (no delegation).
pub fn prepare_graph_args(args: &mut Args) -> Result<Option<GraphContext>, String> {
    if graph_writes_disabled() {
        return Ok(None);
    }
    apply_project_registry_defaults(args);
    match args.graph_provider.as_str() {
        "neo4j" => {
            if args.neo4j_uri.is_some() && args.neo4j_user.is_some() && args.neo4j_password.is_some() {
                Ok(Some(GraphContext {
                    provider: "neo4j".to_string(),
                    falkordb_uri: None,
                    falkordb_path: None,
                    ladybug_path: None,
                    falkordb_graph: args.neo4j_db.clone().unwrap_or_default(),
                    neo4j_db: args.neo4j_db.clone().unwrap_or_default(),
                }))
            } else {
                Ok(None)
            }
        }
        "ladybug" => {
            // Embedded-only. Store path: --ladybug-path arg (cli.rs merges the
            // LADYBUG_PATH env fallback) > resolve_storage (anchored --root).
            let path = match args
                .ladybug_path
                .clone()
                .map(|v| v.trim().to_string())
                .filter(|v| !v.is_empty())
            {
                Some(path) => path,
                None => resolve_embedded_path(args, "code")?.to_string_lossy().to_string(),
            };
            // Graph name — cli.py:249-256 chain, exact: explicit arg >
            // project_id > LADYBUG_GRAPH env > "hyper_graph". The registry
            // NEVER participates for ladybug (red-team H1). Note the live
            // Python argparse pre-fills env/"hyper_graph" making project_id
            // shadowed in the direct path; this port implements the
            // documented chain (dev always passes the flag explicitly, so
            // the user flow is identical).
            let graph = args
                .ladybug_graph
                .clone()
                .map(|v| v.trim().to_string())
                .filter(|v| !v.is_empty())
                .or_else(|| {
                    args.project_id
                        .clone()
                        .map(|v| v.trim().to_string())
                        .filter(|v| !v.is_empty())
                })
                .or_else(|| env_lookup("LADYBUG_GRAPH"))
                .unwrap_or_else(|| "hyper_graph".to_string());
            Ok(Some(GraphContext {
                provider: "ladybug".to_string(),
                falkordb_uri: None,
                falkordb_path: None,
                ladybug_path: Some(path),
                falkordb_graph: graph.clone(),
                neo4j_db: graph,
            }))
        }
        _ => {
            let falkordb_uri = if args.explicit_falkordb_target == Some("path") {
                None
            } else {
                args.falkordb_uri
                    .clone()
                    .or_else(|| env_lookup("FALKORDB_URI"))
                    .map(|v| v.trim().to_string())
                    .filter(|v| !v.is_empty())
            };
            let (falkordb_uri, falkordb_path) = if let Some(uri) = falkordb_uri {
                (Some(uri), None)
            } else {
                // Embedded FalkorDBLite path: explicit arg/env first, then
                // resolve_storage (same derivation as cli.py:265-269).
                match args
                    .falkordb_path
                    .clone()
                    .map(|v| v.trim().to_string())
                    .filter(|v| !v.is_empty())
                {
                    Some(path) => (None, Some(path)),
                    None => (
                        None,
                        Some(resolve_embedded_path(args, "code")?.to_string_lossy().to_string()),
                    ),
                }
            };
            let graph = args
                .falkordb_graph
                .clone()
                .filter(|g| !g.trim().is_empty())
                .unwrap_or_else(|| {
                    args.project_id
                        .clone()
                        .unwrap_or_else(|| "hyper_graph".to_string())
                });
            Ok(Some(GraphContext {
                provider: "falkordb".to_string(),
                falkordb_uri,
                falkordb_path,
                ladybug_path: None,
                falkordb_graph: graph.clone(),
                neo4j_db: graph,
            }))
        }
    }
}

/// Q2 verdict (phase-01.5 spike, red-team H2): embedded FalkorDB stays
/// FAIL-CLOSED in the native plane. Evidence: (a) `falkor_boot.rs` boots
/// redislite with `save ""` + `SHUTDOWN NOSAVE` — a read-only contract;
/// writes would be lost on shutdown; (b) the Rust analyzer children have no
/// embedded FalkorDBLite support at all (cortex-analyzer-framework
/// cli.rs:288-291), so even a writable parent-side boot would strand the
/// write plane. Error states BOTH honest options including the rebuild
/// caveat (no falkordb→ladybug data migration exists).
const EMBEDDED_FALKORDB_UNAVAILABLE: &str = "embedded FalkorDB (FALKORDB_PATH) is not available in the native sync plane: the Rust analyzer children cannot open FalkorDBLite stores and the embedded boot contract is read-only. Options: set FALKORDB_URI to a remote FalkorDB server (keeps the existing data), or set GRAPH_PROVIDER=ladybug (new store; the graph is rebuilt by a full re-sync — there is no data migration from falkordb to ladybug)";

/// Open a polymorphic store for the context (phase-02: ladybug joins the
/// native plane via `LadybugStore`; falkordb remote via `FalkorDbStore`).
pub fn open_store(context: &GraphContext) -> Result<Box<dyn GraphStore>, String> {
    match context.provider.as_str() {
        "ladybug" => {
            let path = context
                .ladybug_path
                .as_deref()
                .filter(|p| !p.trim().is_empty())
                .ok_or_else(|| "ladybug context has no resolved store path".to_string())?;
            let store = LadybugStore::open(std::path::Path::new(path), &context.falkordb_graph)
                .map_err(|error| format!("ladybug open {path} (graph {}): {error}", context.falkordb_graph))?;
            Ok(Box::new(store))
        }
        "falkordb" => {
            let Some(uri) = &context.falkordb_uri else {
                return Err(EMBEDDED_FALKORDB_UNAVAILABLE.to_string());
            };
            let (host, port) = parse_falkordb_uri(uri);
            let mut client = cortex_falkordb::client::FalkorDbClient::connect(&host, port)
                .map_err(|error| format!("falkordb connect {host}:{port}: {error}"))?;
            if let Some(password) = env_lookup("FALKORDB_PASSWORD") {
                redis::cmd("AUTH")
                    .arg(&password)
                    .query::<()>(client.connection_mut())
                    .map_err(|error| format!("falkordb auth: {error}"))?;
            }
            Ok(Box::new(FalkorDbStore::new(client, context.falkordb_graph.clone())))
        }
        "neo4j" => Err(
            "neo4j has no native store in cortex-sync (children write falkordb/ladybug only); set GRAPH_PROVIDER=falkordb (with FALKORDB_URI) or GRAPH_PROVIDER=ladybug"
                .to_string(),
        ),
        other => Err(format!("unsupported graph provider: {other}")),
    }
}

/// `_PROJECT_REPOSITORY_SETUP_QUERY`.
pub const PROJECT_REPOSITORY_SETUP_QUERY: &str = r#"
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
WITH p
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
WITH p, r
MERGE (p)-[:HAS_REPOSITORY]->(r)
"#;

/// `project_id_lookup_key` (tools/common/project_scope.py).
pub fn project_id_lookup_key(value: &str) -> Option<String> {
    let normalized = value.trim();
    if normalized.is_empty() {
        None
    } else {
        Some(normalized.to_string())
    }
}

/// `_ensure_project_repository_graph` — schema preflight + setup mutation.
pub fn ensure_project_repository_graph(
    store: &mut dyn GraphStore,
    database: Option<&str>,
    project_id: &str,
    project_name: &str,
    repo_name: &str,
    verbose: bool,
) -> Result<bool, String> {
    let result = cortex_graph_writer::preflight::ensure_schema(store, database)
        .map_err(|error| error.to_string())?;
    if verbose {
        println!(
            "[schema] ready manifest={} fingerprint={} indexes={} verified={}",
            result.manifest, result.fingerprint, result.required_count, result.verified_count
        );
    }
    let mut params = BTreeMap::new();
    params.insert("project_id".to_string(), json!(project_id));
    params.insert(
        "project_id_normalized".to_string(),
        json!(project_id_lookup_key(project_id)),
    );
    params.insert("project_name".to_string(), json!(project_name));
    params.insert("project_slug".to_string(), json!(crate::util::normalize_slug(project_name)));
    params.insert("repo_name".to_string(), json!(repo_name));
    store
        .execute_query(PROJECT_REPOSITORY_SETUP_QUERY, &params, database)
        .map_err(|error| error.to_string())?;
    Ok(true)
}

/// `_query_impacted_files` — three relationship-expand queries.
pub fn query_impacted_files(
    store: &mut dyn GraphStore,
    database: Option<&str>,
    project_id: &str,
    changed_paths: &BTreeSet<String>,
) -> Result<BTreeSet<String>, String> {
    let mut impacted = BTreeSet::new();
    if changed_paths.is_empty() {
        return Ok(impacted);
    }
    let queries = [
        r#"
        MATCH (src:File)-[r]->(dst:File)
        WHERE src.project_id = $project_id
          AND dst.project_id = $project_id
          AND type(r) IN ["INCLUDES", "DEPENDS_ON", "USES", "USES_TYPE", "EXTENDS", "IMPLEMENTS", "INHERITS", "MIXES_IN"]
          AND dst.id IN $changed_paths
        RETURN DISTINCT src.id AS file_path
        "#,
        r#"
        MATCH (src:File)-[:CONTAINS]->(:Function)-[r]->(:Function)<-[:CONTAINS]-(dst:File)
        WHERE src.project_id = $project_id
          AND dst.project_id = $project_id
          AND type(r) IN ["CALLS", "POSSIBLE_CALLS", "CALLS_FUNCTION_POINTER"]
          AND dst.id IN $changed_paths
        RETURN DISTINCT src.id AS file_path
        "#,
        r#"
        MATCH (src:File)-[:CONTAINS]->(srcNode)-[r]->(dstNode)<-[:CONTAINS]-(dst:File)
        WHERE src.project_id = $project_id
          AND dst.project_id = $project_id
          AND type(r) IN ["USES_TYPE", "EXTENDS", "IMPLEMENTS", "INHERITS", "MIXES_IN", "DEPENDS_ON_TYPE"]
          AND dst.id IN $changed_paths
        RETURN DISTINCT src.id AS file_path
        "#,
    ];
    for query in queries {
        let mut params = BTreeMap::new();
        params.insert("project_id".to_string(), json!(project_id));
        params.insert(
            "changed_paths".to_string(),
            json!(changed_paths.iter().cloned().collect::<Vec<_>>()),
        );
        let rows = store
            .execute_query(query, &params, database)
            .map_err(|error| error.to_string())?;
        for row in rows {
            if let Some(value) = row.get("file_path").and_then(|v| v.as_str()) {
                let file_path = value.trim().replace('\\', "/");
                if !file_path.is_empty() {
                    impacted.insert(file_path);
                }
            }
        }
    }
    Ok(impacted)
}

/// `_project_topology_bootstrap_needed`.
pub fn project_topology_bootstrap_needed(
    store: &mut dyn GraphStore,
    database: Option<&str>,
    project_id: &str,
) -> Result<bool, String> {
    let Some(normalized) = project_id_lookup_key(project_id) else {
        return Ok(true);
    };
    let mut params = BTreeMap::new();
    params.insert("project_id_normalized".to_string(), json!(normalized));
    let rows = store
        .execute_query(
            "MATCH (m:ProjectModule) WHERE m.project_id_normalized = $project_id_normalized RETURN count(m) AS total",
            &params,
            database,
        )
        .map_err(|error| error.to_string())?;
    let total = rows
        .first()
        .and_then(|row| row.get("total"))
        .and_then(|value| {
            value
                .as_i64()
                .or_else(|| value.as_str().and_then(|s| s.parse().ok()))
        })
        .unwrap_or(0);
    Ok(total == 0)
}

/// `_graph_target_cli_args` — pin children to the resolved graph target.
/// The remote URI is NOT passed as a flag: the Python parent relies on the
/// propagated `FALKORDB_URI`/`FALKORDB_GRAPH` env, and children re-read them.
pub fn graph_target_cli_args(args: &Args, context: &GraphContext) -> Vec<String> {
    let mut result = vec!["--graph-provider".to_string(), context.provider.clone()];
    match context.provider.as_str() {
        "falkordb" => {
            let graph = context.falkordb_graph.trim().to_string();
            if !graph.is_empty() {
                result.push("--falkordb-graph".to_string());
                result.push(graph);
            }
        }
        // incremental_sync.py:1434-1440 — children nhận ladybug target qua
        // flags (không dựa vào env kế thừa).
        "ladybug" => {
            if let Some(path) = context
                .ladybug_path
                .as_deref()
                .map(str::trim)
                .filter(|p| !p.is_empty())
            {
                result.push("--ladybug-path".to_string());
                result.push(path.to_string());
            }
            let graph = context.falkordb_graph.trim().to_string();
            if !graph.is_empty() {
                result.push("--ladybug-graph".to_string());
                result.push(graph);
            }
        }
        _ => {}
    }
    let _ = args;
    result
}

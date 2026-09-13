//! Graph-plane helpers: `prepare_graph_args`, driver-style connection over
//! `cortex-falkordb`, `ensure_schema` + Project/Repository setup, the impact
//! expansion queries, and the topology bootstrap probe.

use std::collections::{BTreeMap, BTreeSet};

use cortex_graph_writer::store::{FalkorDbStore, GraphStore};
use serde_json::json;

use crate::cli::Args;

pub struct GraphContext {
    pub provider: String,
    pub falkordb_uri: Option<String>,
    pub falkordb_path: Option<String>,
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

/// `apply_project_registry_defaults` — Python-plane. The ProjectRegistry
/// lookup is not ported; when a harness config dir exists under root the
/// registry behaves as "not registered" (warning + fallback), which is the
/// parity-corpus behavior.
fn apply_project_registry_defaults(args: &Args) {
    let root = std::path::PathBuf::from(&args.root);
    if root.join(".cortext-harness/config").is_dir() {
        eprintln!(
            "[graph-cli] project_id {:?} not registered in discovered config; falling back to args.project_id for falkordb-graph.",
            args.project_id
        );
    }
}

/// `prepare_graph_args` — resolve the effective graph target. Returns None
/// when graph writes are disabled; Err means the embedded-storage fallback
/// (resolve_storage) would be needed, which is Python-plane and the caller
/// must delegate the run to the Python orchestrator.
pub fn prepare_graph_args(args: &Args) -> Result<Option<GraphContext>, String> {
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
                    falkordb_graph: args.neo4j_db.clone().unwrap_or_default(),
                    neo4j_db: args.neo4j_db.clone().unwrap_or_default(),
                }))
            } else {
                Ok(None)
            }
        }
        "ladybug" => {
            // Embedded-only; the ladybug store path derivation is Python-plane.
            Err("ladybug provider requires embedded storage resolution (Python-plane)".to_string())
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
            } else if let Some(path) = args.falkordb_path.clone().filter(|p| !p.trim().is_empty()) {
                (None, Some(path))
            } else {
                return Err(
                    "embedded FalkorDB storage resolution (resolve_storage) is Python-plane; set FALKORDB_URI or --falkordb-path"
                        .to_string(),
                );
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
                falkordb_graph: graph.clone(),
                neo4j_db: graph,
            }))
        }
    }
}

/// Open a `FalkorDbStore` for the context.
pub fn open_store(context: &GraphContext) -> Result<FalkorDbStore, String> {
    let Some(uri) = &context.falkordb_uri else {
        return Err("embedded FalkorDB (FALKORDB_PATH) driver is Python-plane".to_string());
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
    Ok(FalkorDbStore::new(client, context.falkordb_graph.clone()))
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
    store: &mut FalkorDbStore,
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
    store: &mut FalkorDbStore,
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
    store: &mut FalkorDbStore,
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
pub fn graph_target_cli_args(_args: &Args, context: &GraphContext) -> Vec<String> {
    let mut result = vec!["--graph-provider".to_string(), context.provider.clone()];
    if context.provider == "falkordb" {
        let graph = context.falkordb_graph.trim().to_string();
        if !graph.is_empty() {
            result.push("--falkordb-graph".to_string());
            result.push(graph);
        }
    }
    result
}

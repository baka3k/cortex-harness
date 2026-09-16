//! Doc-graph reads for the mind tools — port of `doc-tiny/graph_store.py`
//! (`FalkorDBGraphStore.session().run`) plus the mind-layer helpers of
//! `doc-tiny/mcp_graph_rag.py` (`_graph_store_candidates`, `get_neo4j`,
//! `fetch_entities_by_ids`, `fetch_relations_by_entity_ids`,
//! `fetch_relations_with_depth`, `fetch_paragraph_by_source`, and the
//! `list_source_ids` query).
//!
//! Connection resolution mirrors `get_neo4j`:
//! * registered project + `storage_backend: remote` + `remote.falkordb_uri`
//!   → remote server (the factory driver);
//! * registered project otherwise / unregistered fallback → the env-seeded
//!   base store (`FALKORDB_URI` env remote server; the file-backed embedded
//!   engine has no wire protocol and is a documented Python-plane exclusion);
//!   the graph name follows `targets.doc_graph` / the `{id}_doc` convention.
//!
//! Reads run through `GRAPH.QUERY` (like the Python driver's
//! `execute_query_sync`, which auto-creates an empty graph for a missing
//! name and returns no rows), so missing-graph behavior matches the
//! reference byte-for-byte, stray-graph side effect included.

use std::collections::{BTreeMap, BTreeSet};

use cortex_falkordb::client::{ClientError, FalkorDbClient, Param};
use cortex_falkordb::normalize::normalize_value;
use serde_json::{Map, Value};

use super::qdrant::QdrantError;

/// Exception-shaped error mirroring the Python tool body.
#[derive(Debug)]
pub struct GraphError {
    pub exception: &'static str,
    pub message: String,
}

impl From<ClientError> for GraphError {
    fn from(error: ClientError) -> Self {
        // redis-py surfaces server errors as plain messages; keep the same
        // text (`str(exc)` of the driver exception).
        GraphError { exception: "RuntimeError", message: error.to_string() }
    }
}

/// `normalize_driver_error` of the graph module — rust-redis renders server
/// errors `redis: "<kind>": <detail>`; redis-py keeps `<kind> <detail>`.
pub fn normalize_driver_error(message: &str) -> String {
    crate::graph::normalize_driver_error(message)
}

/// One FalkorDB-backed graph view (client + graph name), like
/// `FalkorDBGraphStore`.
pub struct DocGraphStore {
    backend: DocBackend,
    graph: String,
}

enum DocBackend {
    Falkor(FalkorDbClient),
    Ladybug(crate::graph::ladybug::LadybugStore),
}

impl DocGraphStore {
    pub fn graph(&self) -> &str {
        &self.graph
    }

    /// `FalkorDBSession.run` — write-variant query, records as maps.
    pub fn run(
        &mut self,
        cypher: &str,
        params: BTreeMap<String, Param>,
    ) -> Result<Vec<Map<String, Value>>, GraphError> {
        match &mut self.backend {
            DocBackend::Falkor(client) => {
                let result = client.query(&self.graph, cypher, &params, None)?;
                let names: Vec<String> =
                    result.header.iter().map(|column| column.name.clone()).collect();
                Ok(result
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
                    .collect())
            }
            DocBackend::Ladybug(store) => {
                store.query(cypher, &params).map_err(|error| GraphError {
                    exception: "RuntimeError",
                    message: error,
                })
            }
        }
    }
}

fn is_ladybug_provider() -> bool {
    let provider = std::env::var("DOC_GRAPH_PROVIDER")
        .or_else(|_| std::env::var("GRAPH_PROVIDER"))
        .unwrap_or_default();
    matches!(provider.trim().to_lowercase().as_str(), "ladybug" | "ladybugdb" | "kuzu")
        || std::env::var("LADYBUG_DOC_PATH")
            .map(|value| !value.trim().is_empty())
            .unwrap_or(false)
}

/// `<owner>.lbug` directory holding the doc graph store files —
/// `LADYBUG_DOC_PATH`'s parent, else the storage layout default.
fn ladybug_doc_dir() -> Option<std::path::PathBuf> {
    if let Some(primary) = crate::graph::ladybug::env_primary_store("doc") {
        return primary.parent().map(std::path::Path::to_path_buf);
    }
    let home = std::env::var("CORTEX_DATA_HOME").ok().filter(|v| !v.trim().is_empty())
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| {
            let mut home = std::env::var("HOME")
                .map(std::path::PathBuf::from)
                .unwrap_or_default();
            home.push(".cortext-harness");
            home
        });
    let instance = std::env::var("CORTEX_STORAGE_INSTANCE")
        .map(|v| v.trim().to_lowercase().replace('.', "-"))
        .ok()
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| "default".to_string());
    Some(
        home.join("v1")
            .join("instances")
            .join(instance)
            .join("ladybug")
            .join("doc")
            .join("doc.lbug"),
    )
}

/// Mirror of `cortex_storage::layout::ladybug_store_file_name` — a graph name
/// becomes a store FILE name; names outside `[A-Za-z0-9_.-]` fail closed.
fn ladybug_store_file_name(graph_name: &str) -> Option<String> {
    let name = graph_name.trim();
    if name.is_empty() || !name.chars().all(|ch| ch.is_alphanumeric() || "_-.".contains(ch)) {
        return None;
    }
    Some(name.to_string())
}

/// Open the doc ladybug store for one graph file, `None` when the store file
/// does not exist yet (python raises `database does not exist` at query time;
/// callers skip the candidate — observable parity for the fan-out lanes).
fn ladybug_doc_store(graph: &str) -> Result<Option<DocGraphStore>, GraphError> {
    let Some(dir) = ladybug_doc_dir() else {
        return Err(GraphError {
            exception: "ConnectionError",
            message: "no ladybug doc store configured (LADYBUG_DOC_PATH)".to_string(),
        });
    };
    let Some(file_name) = ladybug_store_file_name(graph) else {
        return Err(GraphError {
            exception: "RuntimeError",
            message: format!("Ladybug graph name {graph:?} must only contain letters, digits, '_', '-', or '.'"),
        });
    };
    let path = dir.join(&file_name);
    if !path.is_file() {
        return Ok(None);
    }
    let store = crate::graph::ladybug::LadybugStore::open(&path, false)
        .or_else(|_| crate::graph::ladybug::LadybugStore::open(&path, true))
        .map_err(|error| GraphError { exception: "ConnectionError", message: error })?;
    Ok(Some(DocGraphStore {
        backend: DocBackend::Ladybug(store),
        graph: graph.to_string(),
    }))
}

// ---------------------------------------------------------------------------
// Connection resolution (one client per store, like the Python driver)
// ---------------------------------------------------------------------------

/// `parse_uri` — `redis://host:port`, `host:port` or bare host (port 6379).
pub fn parse_uri(uri: &str) -> (String, u16) {
    let trimmed = uri.trim();
    let without_scheme = trimmed
        .split_once("://")
        .map(|(_, rest)| rest)
        .unwrap_or(trimmed);
    let without_scheme = without_scheme.split('/').next().unwrap_or(without_scheme);
    match without_scheme.rsplit_once(':') {
        Some((host, port)) => (host.to_string(), port.parse().unwrap_or(6379)),
        None => (without_scheme.to_string(), 6379),
    }
}

fn connect(host: &str, port: u16) -> Result<FalkorDbClient, GraphError> {
    Ok(FalkorDbClient::connect_verified(host, port)?)
}

/// Env-seeded base connection (`create_graph_store_from_env`, falkordb
/// provider, remote URI only).
fn env_falkordb_uri() -> Option<(String, u16)> {
    let raw = std::env::var("FALKORDB_URI").ok()?;
    let uri = raw.trim();
    if uri.is_empty() {
        return None;
    }
    Some(parse_uri(uri))
}

/// `get_neo4j(project_id)` — one store bound to the project's doc graph.
///
/// Reference behavior (verified): an id that is not EXACTLY registered
/// (casefold) raises `ProjectNotRegisteredError` — the naming-convention
/// fallback in `get_neo4j` is dead code (doc-tiny catches a different
/// same-named exception class than the code-tiny registry raises; see the
/// parity report).
pub fn project_store(project_id: Option<&str>) -> Result<DocGraphStore, GraphError> {
    if let Some(project_id) = project_id.map(str::trim).filter(|value| !value.is_empty()) {
        let targets = crate::project_registry::resolve_project_targets(Some(project_id), None)
            .map_err(|error| GraphError {
                exception: "ProjectNotRegisteredError",
                message: error.to_string(),
            })?;
        // Registered → factory driver honoring the remote section.
        if targets.storage_backend == "remote"
            && let Some(uri) = targets
                .remote_config
                .as_ref()
                .and_then(|remote| remote.get("falkordb_uri"))
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
        {
            let (host, port) = parse_uri(uri);
            let client = connect(&host, port)?;
            return Ok(DocGraphStore { backend: DocBackend::Falkor(client), graph: targets.doc_graph });
        }
        if is_ladybug_provider() {
            // Ladybug primary: the env-seeded store file — python opens
            // `LADYBUG_PATH` regardless of the graph name when the per-graph
            // store file does not exist yet.
            if let Some(store) = ladybug_doc_store(&targets.doc_graph)? {
                return Ok(store);
            }
            if let Some(primary) = crate::graph::ladybug::env_primary_store("doc")
                && primary.is_file()
            {
                let store = crate::graph::ladybug::LadybugStore::open(&primary, false)
                    .or_else(|_| {
                        crate::graph::ladybug::LadybugStore::open(&primary, true)
                    })
                    .map_err(|error| GraphError {
                        exception: "ConnectionError",
                        message: error,
                    })?;
                return Ok(DocGraphStore {
                    backend: DocBackend::Ladybug(store),
                    graph: targets.doc_graph,
                });
            }
            return Err(GraphError {
                exception: "RuntimeError",
                message: format!(
                    "database does not exist: {} (no LadybugDB store in {})",
                    targets.doc_graph,
                    ladybug_doc_dir()
                        .map(|dir| dir.display().to_string())
                        .unwrap_or_default(),
                ),
            });
        }
        let (host, port) = env_falkordb_uri().ok_or_else(|| GraphError {
            exception: "ConnectionError",
            message: "no falkordb_uri configured (remote section or FALKORDB_URI)".to_string(),
        })?;
        let client = connect(&host, port)?;
        return Ok(DocGraphStore { backend: DocBackend::Falkor(client), graph: targets.doc_graph });
    }
    // Unscoped base store (`create_graph_store_from_env`).
    if is_ladybug_provider() {
        let Some(primary) = crate::graph::ladybug::env_primary_store("doc") else {
            return Err(GraphError {
                exception: "ConnectionError",
                message: "no ladybug doc store configured (LADYBUG_DOC_PATH)".to_string(),
            });
        };
        let graph = primary
            .file_stem()
            .map(|stem| stem.to_string_lossy().to_string())
            .unwrap_or_else(|| "hyper_graph".to_string());
        let store = crate::graph::ladybug::LadybugStore::open(&primary, false)
            .or_else(|_| crate::graph::ladybug::LadybugStore::open(&primary, true))
            .map_err(|error| GraphError { exception: "ConnectionError", message: error })?;
        return Ok(DocGraphStore { backend: DocBackend::Ladybug(store), graph });
    }
    let (host, port) = env_falkordb_uri().ok_or_else(|| GraphError {
        exception: "ConnectionError",
        message: "no falkordb_uri configured (FALKORDB_URI)".to_string(),
    })?;
    let client = connect(&host, port)?;
    let graph = std::env::var("FALKORDB_GRAPH")
        .or_else(|_| std::env::var("FALKORDB_DATABASE"))
        .unwrap_or_else(|_| "neo4j".to_string());
    Ok(DocGraphStore { backend: DocBackend::Falkor(client), graph })
}

/// `_graph_store_candidates(project_id)` — the deterministic store list for
/// scoped or full-search reads (falkordb provider only; the neo4j provider
/// keeps request-scoped sessions that this port does not exercise).
pub fn graph_store_candidates(
    project_id: Option<&str>,
) -> Result<Vec<(DocGraphStore, bool)>, GraphError> {
    if let Some(project_id) = project_id.map(str::trim).filter(|value| !value.is_empty()) {
        let matched =
            crate::project_registry::resolve_project_scope_candidates(Some(project_id), None)
                .map_err(|error| GraphError {
                    exception: "ProjectNotRegisteredError",
                    message: error.to_string(),
                })?;
        if matched.len() <= 1 {
            let id = matched
                .first()
                .map(|targets| targets.project_id.clone())
                .unwrap_or_else(|| project_id.to_string());
            return Ok(vec![(project_store(Some(&id))?, false)]);
        }
        let mut stores = Vec::new();
        for targets in &matched {
            stores.push((project_store(Some(&targets.project_id))?, false));
        }
        return Ok(stores);
    }
    // Unscoped: one store per registered project doc graph (dedup, order of
    // the registry listing), like `base.for_graph(name)` over one shared
    // driver — the Rust port opens one connection per view.
    let registered = crate::project_registry::list_registered_projects(None).map_err(|error| {
        GraphError { exception: "ProjectNotRegisteredError", message: error.to_string() }
    })?;
    let mut graph_names: Vec<String> = Vec::new();
    for project in &registered {
        let targets = crate::project_registry::resolve_project_targets(Some(project), None)
            .map_err(|error| GraphError {
                exception: "ProjectNotRegisteredError",
                message: error.to_string(),
            })?;
        if !targets.doc_graph.is_empty() && !graph_names.contains(&targets.doc_graph) {
            graph_names.push(targets.doc_graph);
        }
    }
    if graph_names.is_empty() {
        return Ok(vec![(project_store(None)?, false)]);
    }
    if is_ladybug_provider() {
        // Ladybug fan-out: one store FILE per graph; missing files are
        // skipped (python raises `database does not exist` at query time,
        // which the fail-soft lanes skip identically).
        let mut stores = Vec::new();
        for graph in graph_names {
            if let Some(store) = ladybug_doc_store(&graph)? {
                stores.push((store, false));
            }
        }
        return Ok(stores);
    }
    let (host, port) = env_falkordb_uri().ok_or_else(|| GraphError {
        exception: "ConnectionError",
        message: "no falkordb_uri configured (FALKORDB_URI)".to_string(),
    })?;
    Ok(graph_names
        .into_iter()
        .map(|graph| {
            let client = connect(&host, port).expect("falkordb connect");
            (DocGraphStore { backend: DocBackend::Falkor(client), graph }, false)
        })
        .collect())
}

/// The `project_id_normalized` query param (`project_id.strip().casefold()`).
pub fn normalized_param(project_id: Option<&str>) -> Param {
    match project_id.map(str::trim).filter(|value| !value.is_empty()) {
        Some(id) => Param::Str(
            cortex_graph_writer::project_scope::project_id_lookup_key(Some(id))
                .unwrap_or_else(|| id.to_string()),
        ),
        None => Param::Null,
    }
}

// ---------------------------------------------------------------------------
// Mind-layer graph helpers (mcp_graph_rag.py)
// ---------------------------------------------------------------------------

fn params_map(entries: Vec<(&str, Param)>) -> BTreeMap<String, Param> {
    entries.into_iter().map(|(key, value)| (key.to_string(), value)).collect()
}

/// `fetch_entities_by_ids`.
pub fn fetch_entities_by_ids(
    entity_ids: &[String],
    project_id: Option<&str>,
) -> Result<Vec<Map<String, Value>>, GraphError> {
    if entity_ids.is_empty() {
        return Ok(Vec::new());
    }
    let mut entities: Vec<Map<String, Value>> = Vec::new();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    // Ladybug binder types an unbound parameter BOOL on `IS NULL`, then
    // rejects the STRING use in STARTS WITH — branch the query instead of
    // ever passing a NULL string param (mirror of the python fix).
    let project_key = project_id.map(str::trim).filter(|value| !value.is_empty());
    let query = match project_key {
        Some(_) => "\n                    MATCH (e:Entity)\n                    WHERE e.id IN $ids\n                      AND e.project_id_normalized STARTS WITH $project_id_normalized\n                    RETURN e.id AS id, e.name AS name, e.type AS type\n                    ",
        None => "\n                    MATCH (e:Entity)\n                    WHERE e.id IN $ids\n                    RETURN e.id AS id, e.name AS name, e.type AS type\n                    ",
    };
    for (store, _owned) in graph_store_candidates(project_id)? {
        let mut store = store;
        let mut params_vec = vec![
            ("ids", Param::List(entity_ids.iter().map(|id| Param::Str(id.clone())).collect())),
        ];
        if let Some(key) = project_key {
            params_vec.push(("project_id_normalized", Param::Str(key.to_string())));
        }
        let params = params_map(params_vec);
        let result = store.run(query, params);
        let rows = result?;
        for row in rows {
            let key = match row.get("id").and_then(Value::as_str) {
                Some(id) => format!("id:{id}"),
                None => format!(
                    "nt:{}:{}",
                    row.get("name").and_then(Value::as_str).unwrap_or(""),
                    row.get("type").and_then(Value::as_str).unwrap_or("")
                ),
            };
            if seen.insert(key) {
                entities.push(row);
            }
        }
    }
    entities.truncate(entity_ids.len());
    Ok(entities)
}

/// `fetch_relations_by_entity_ids` — loops over the store candidates with a
/// shared `related_k` budget (mirrors the python body exactly).
#[allow(clippy::too_many_arguments)]
pub fn fetch_relations_by_entity_ids(
    entity_ids: &[String],
    entity_types: &[Value],
    related_k: usize,
    project_id: Option<&str>,
) -> Result<Vec<Map<String, Value>>, GraphError> {
    if entity_ids.is_empty() || related_k == 0 {
        return Ok(Vec::new());
    }
    let project_key = project_id.map(str::trim).filter(|value| !value.is_empty());
    let query = match project_key {
        Some(_) => "\n                    UNWIND $ids AS id\n                    MATCH (e:Entity {id: id})-[r:RELATED]-(e2:Entity)\n                    WHERE ($types = [] OR e.type IN $types OR e2.type IN $types)\n                      AND ((e.project_id_normalized STARTS WITH $project_id_normalized AND\n                            e2.project_id_normalized STARTS WITH $project_id_normalized))\n                    RETURN e.id AS source_id, e.name AS source, e.type AS source_type,\n                           r.type AS relation,\n                           e2.id AS target_id, e2.name AS target, e2.type AS target_type\n                    LIMIT $limit\n                    ",
        None => "\n                    UNWIND $ids AS id\n                    MATCH (e:Entity {id: id})-[r:RELATED]-(e2:Entity)\n                    WHERE ($types = [] OR e.type IN $types OR e2.type IN $types)\n                    RETURN e.id AS source_id, e.name AS source, e.type AS source_type,\n                           r.type AS relation,\n                           e2.id AS target_id, e2.name AS target, e2.type AS target_type\n                    LIMIT $limit\n                    ",
    };
    let mut relations: Vec<Map<String, Value>> = Vec::new();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    for (store, _owned) in graph_store_candidates(project_id)? {
        let remaining = related_k - relations.len();
        if remaining == 0 {
            break;
        }
        let mut store = store;
        let mut params_vec = vec![
            ("ids", Param::List(entity_ids.iter().map(|id| Param::Str(id.clone())).collect())),
            ("types", Param::List(entity_types.iter().map(value_to_param).collect())),
            ("limit", Param::Int(remaining as i64)),
        ];
        if let Some(key) = project_key {
            params_vec.push(("project_id_normalized", Param::Str(key.to_string())));
        }
        let params = params_map(params_vec);
        let rows = store.run(query, params)?;
        for row in rows {
            let key = format!(
                "{}|{}|{}",
                row.get("source_id").and_then(Value::as_str).unwrap_or(""),
                row.get("relation").and_then(Value::as_str).unwrap_or(""),
                row.get("target_id").and_then(Value::as_str).unwrap_or("")
            );
            if seen.insert(key) {
                relations.push(row);
                if relations.len() >= related_k {
                    break;
                }
            }
        }
        let _ = _owned;
    }
    Ok(relations)
}

fn value_to_param(value: &Value) -> Param {
    crate::graph::runtime::json_to_param(value)
}

/// `fetch_relations_with_depth` — BFS expansion up to `depth` hops.
pub fn fetch_relations_with_depth(
    entity_ids: &[String],
    entity_types: &[Value],
    related_k: usize,
    depth: usize,
    project_id: Option<&str>,
) -> Result<Vec<Map<String, Value>>, GraphError> {
    if entity_ids.is_empty() || related_k == 0 || depth == 0 {
        return Ok(Vec::new());
    }
    let mut relations: Vec<Map<String, Value>> = Vec::new();
    let mut seen_relations: BTreeSet<String> = BTreeSet::new();
    let mut seen_entities: BTreeSet<String> = entity_ids.iter().cloned().collect();
    let mut frontier: Vec<String> = entity_ids.to_vec();
    let mut remaining = related_k;

    for _ in 0..depth {
        if frontier.is_empty() || remaining == 0 {
            break;
        }
        let step_relations =
            fetch_relations_by_entity_ids(&frontier, entity_types, remaining, project_id)?;
        // Python builds `new_frontier` as a set; the frontier ORDER after a
        // hop is set-iteration order (implementation-defined). We keep
        // insertion order (deterministic) and mask relation ordering in the
        // comparator for depth ≥ 2.
        let mut new_frontier: Vec<String> = Vec::new();
        for rel in &step_relations {
            let rel_key = format!(
                "{}|{}|{}",
                rel.get("source_id").and_then(Value::as_str).unwrap_or(""),
                rel.get("relation").and_then(Value::as_str).unwrap_or(""),
                rel.get("target_id").and_then(Value::as_str).unwrap_or("")
            );
            if seen_relations.contains(&rel_key) {
                continue;
            }
            seen_relations.insert(rel_key);
            relations.push(rel.clone());
            for slot in ["source_id", "target_id"] {
                if let Some(id) = rel.get(slot).and_then(Value::as_str)
                    && !id.is_empty()
                    && !seen_entities.contains(id)
                {
                    seen_entities.insert(id.to_string());
                    new_frontier.push(id.to_string());
                }
            }
        }
        remaining = related_k.saturating_sub(relations.len());
        if new_frontier.is_empty() {
            break;
        }
        frontier = new_frontier;
    }
    Ok(relations)
}

/// `fetch_paragraph_by_source`.
pub fn fetch_paragraph_by_source(
    source_id: &str,
    paragraph_id: i64,
    project_id: Option<&str>,
) -> Result<Option<Map<String, Value>>, GraphError> {
    let project_key = project_id.map(str::trim).filter(|value| !value.is_empty());
    let query = match project_key {
        Some(_) => "\n                    MATCH (p:Paragraph {source_id: $source_id, paragraph_id: $paragraph_id})\n                    WHERE p.project_id_normalized STARTS WITH $project_id_normalized\n                    RETURN p.text AS text,\n                           p.short AS short,\n                           p.source_id AS source_id,\n                           p.paragraph_id AS paragraph_id\n                    ",
        None => "\n                    MATCH (p:Paragraph {source_id: $source_id, paragraph_id: $paragraph_id})\n                    RETURN p.text AS text,\n                           p.short AS short,\n                           p.source_id AS source_id,\n                           p.paragraph_id AS paragraph_id\n                    ",
    };
    for (store, _owned) in graph_store_candidates(project_id)? {
        let mut store = store;
        let mut params_vec = vec![
            ("source_id", Param::Str(source_id.to_string())),
            ("paragraph_id", Param::Int(paragraph_id)),
        ];
        if let Some(key) = project_key {
            params_vec.push(("project_id_normalized", Param::Str(key.to_string())));
        }
        let params = params_map(params_vec);
        let rows = store.run(query, params)?;
        let _ = _owned;
        // `result.single()` — first row or None.
        if let Some(row) = rows.into_iter().next() {
            return Ok(Some(row));
        }
    }
    Ok(None)
}

/// `list_source_ids` graph lane — DISTINCT ordered source ids per store.
pub fn list_source_ids_graph(
    limit: usize,
    project_id: Option<&str>,
) -> Result<Vec<String>, GraphError> {
    let mut source_ids: Vec<String> = Vec::new();
    let project_key = project_id.map(str::trim).filter(|value| !value.is_empty());
    let query = match project_key {
        Some(_) => "\n                        MATCH (p:Paragraph)\n                        WHERE p.source_id IS NOT NULL\n                          AND p.project_id_normalized STARTS WITH $project_id_normalized\n                        RETURN DISTINCT p.source_id AS source_id\n                        ORDER BY source_id\n                        LIMIT $limit\n                        ",
        None => "\n                        MATCH (p:Paragraph)\n                        WHERE p.source_id IS NOT NULL\n                        RETURN DISTINCT p.source_id AS source_id\n                        ORDER BY source_id\n                        LIMIT $limit\n                        ",
    };
    for (store, _owned) in graph_store_candidates(project_id)? {
        if source_ids.len() >= limit {
            break;
        }
        let mut store = store;
        let mut params_vec = vec![("limit", Param::Int((limit - source_ids.len()) as i64))];
        if let Some(key) = project_key {
            params_vec.push(("project_id_normalized", Param::Str(key.to_string())));
        }
        let params = params_map(params_vec);
        // Fail-soft fan-out: a registered project whose doc store was never
        // created must not fail the whole listing (mirror of the python fix).
        let rows = match store.run(query, params) {
            Ok(rows) => rows,
            Err(_) => continue,
        };
        for row in rows {
            if let Some(source_id) = row.get("source_id").and_then(Value::as_str)
                && !source_ids.iter().any(|existing| existing == source_id)
            {
                source_ids.push(source_id.to_string());
            }
        }
    }
    source_ids.truncate(limit);
    Ok(source_ids)
}

/// `QdrantError`-shaped re-export so tools can share the error type.
pub type StoreError = QdrantError;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uri_parsing_matches_python() {
        assert_eq!(parse_uri("redis://127.0.0.1:6379"), ("127.0.0.1".into(), 6379));
        assert_eq!(parse_uri("localhost:6380"), ("localhost".into(), 6380));
        assert_eq!(parse_uri("localhost"), ("localhost".into(), 6379));
    }

    #[test]
    fn normalized_param_casefolds() {
        match normalized_param(Some(" MindFix ")) {
            Param::Str(value) => assert_eq!(value, "mindfix"),
            other => panic!("unexpected param: {other:?}"),
        }
        assert!(matches!(normalized_param(None), Param::Null));
        assert!(matches!(normalized_param(Some("  ")), Param::Null));
    }
}

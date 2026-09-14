//! Port của `code-tiny/tools/common/project_registry.py` — ProjectRegistry,
//! single source of truth for `project_id` → storage targets.
//!
//! Reuses `cortex_graph_writer::project_scope` (`normalize_project_id`,
//! `project_id_lookup_key`) per the phase-11 no-duplication rule.
//!
//! Naming contract (applied when config omits a field):
//! * `code_graph == project_id`
//! * `code_qdrant_collection == project_id`
//! * `doc_graph == "{project_id}_doc"` (separate graph; disjoint labels)
//! * `doc_qdrant_collection == "{project_id}_doc"`
//!
//! The registry reads `.cortext-harness/config/*.json` on every call (no
//! cache) — accepted trade-off, mirroring the Python registry.

use std::path::{Path, PathBuf};

use serde_json::Value;

use cortex_graph_writer::project_scope::{normalize_project_id, project_id_lookup_key};

/// Default location for the harness config dir. Overridable for tests.
pub const DEFAULT_CONFIG_DIRNAME: &str = ".cortext-harness/config";

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/// `ProjectNotRegisteredError` — raised when no config describes `project_id`
/// and no override is given. `Display` reproduces the Python message
/// byte-for-byte.
#[derive(Debug, Clone)]
pub struct ProjectNotRegisteredError {
    /// Raw identifier as passed by the caller (`None` mirrors Python `None`).
    pub project_id: Option<String>,
    /// Sorted list of registered project ids.
    pub known: Vec<String>,
}

impl ProjectNotRegisteredError {
    pub fn new(project_id: Option<&str>, known: Vec<String>) -> Self {
        ProjectNotRegisteredError {
            project_id: project_id.map(str::to_string),
            known,
        }
    }

    /// Python `sorted(known)`.
    fn sorted_known(&self) -> Vec<String> {
        let mut known = self.known.clone();
        known.sort();
        known
    }

    /// Canonical error message (identical to the Python f-string).
    pub fn message(&self) -> String {
        let known = self.sorted_known();
        let rendered = if known.is_empty() {
            "<none registered>".to_string()
        } else {
            known.join(", ")
        };
        let raw = self.project_id.as_deref().unwrap_or("None");
        format!(
            "project_id '{raw}' is not registered. Known projects: {rendered}. \
             Add a project section to .cortext-harness/config/*.json or pass \
             an explicit override."
        )
    }
}

impl std::fmt::Display for ProjectNotRegisteredError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message())
    }
}

/// `DuplicateProjectRegistrationError` — config files declare the same
/// case-insensitive project id.
#[derive(Debug, Clone)]
pub struct DuplicateProjectRegistrationError {
    /// lookup key → sorted registered ids.
    pub collisions: Vec<(String, Vec<String>)>,
}

impl DuplicateProjectRegistrationError {
    pub fn message(&self) -> String {
        let details = self
            .collisions
            .iter()
            .map(|(key, values)| format!("{key}: {}", values.join(", ")))
            .collect::<Vec<_>>()
            .join("; ");
        format!(
            "Duplicate project registrations after casefold normalization: \
             {details}. Keep exactly one config descriptor per logical project."
        )
    }
}

impl std::fmt::Display for DuplicateProjectRegistrationError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message())
    }
}

/// Registry failure union used by the resolution API.
#[derive(Debug, Clone)]
pub enum ProjectRegistryError {
    NotRegistered(ProjectNotRegisteredError),
    Duplicate(DuplicateProjectRegistrationError),
}

impl std::fmt::Display for ProjectRegistryError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ProjectRegistryError::NotRegistered(error) => error.fmt(formatter),
            ProjectRegistryError::Duplicate(error) => error.fmt(formatter),
        }
    }
}

// ---------------------------------------------------------------------------
// ProjectTargets — the resolved contract returned to callers
// ---------------------------------------------------------------------------

/// All storage targets for one project, resolved through the registry.
#[derive(Debug, Clone, PartialEq)]
pub struct ProjectTargets {
    /// Canonical raw project id (registered spelling wins).
    pub project_id: String,
    /// casefold() comparison key.
    pub project_id_normalized: String,
    pub code_graph: String,
    pub code_qdrant_collection: String,
    pub doc_graph: String,
    pub doc_qdrant_collection: String,
    pub parser_type: Option<String>,
    pub provider: String,
    pub storage_backend: String,
    pub remote_config: Option<Value>,
    /// Source of the resolution — diagnostics only.
    pub source: String,
}

// ---------------------------------------------------------------------------
// Provider normalization
// ---------------------------------------------------------------------------

const FALKORDB_PROVIDER_ALIASES: [&str; 4] = ["falkordb", "falkor", "local", "embedded"];
const NEO4J_PROVIDER_ALIASES: [&str; 2] = ["neo4j", "neo"];
const LADYBUG_PROVIDER_ALIASES: [&str; 4] = ["ladybug", "lbug", "lady-bug", "kuzu"];

/// `_normalize_graph_provider` — defaults to falkordb; raises `ValueError`
/// (mirrored as `Err(String)`) for unsupported values.
pub fn normalize_graph_provider(value: Option<&str>) -> Result<String, String> {
    let normalized = value
        .unwrap_or("falkordb")
        .trim()
        .to_lowercase();
    if FALKORDB_PROVIDER_ALIASES.contains(&normalized.as_str()) {
        return Ok("falkordb".to_string());
    }
    if NEO4J_PROVIDER_ALIASES.contains(&normalized.as_str()) {
        return Ok("neo4j".to_string());
    }
    if LADYBUG_PROVIDER_ALIASES.contains(&normalized.as_str()) {
        // "kuzu" predates the LadybugDB fork and resolves onto it.
        return Ok("ladybug".to_string());
    }
    Err(format!(
        "Unsupported graph provider '{}'. Expected 'falkordb', 'ladybug', or 'neo4j'.",
        value.unwrap_or_default()
    ))
}

fn first_env(names: &[&str]) -> Option<String> {
    for name in names {
        if let Ok(value) = std::env::var(name)
            && !value.is_empty()
        {
            return Some(value);
        }
    }
    None
}

// ---------------------------------------------------------------------------
// Loaders
// ---------------------------------------------------------------------------

/// Field on a project config entry that names the project (mirrors dev.json).
const PROJECT_NAME_KEY: &str = "name";
/// Field that holds the canonical project_id (used as the code graph shard
/// name and as the registry key).
const PROJECT_CODE_KEY: &str = "code";

/// Env vars consulted as ad-hoc overrides when a project has no config entry.
const ENV_FALKOR_CODE_GRAPH: &str = "FALKORDB_GRAPH";
const ENV_NEO4J_CODE_GRAPH: &str = "NEO4J_DB";
const ENV_LADYBUG_CODE_GRAPH: &str = "LADYBUG_GRAPH";
const ENV_CODE_COLLECTION: &str = "QDRANT_COLLECTION";
const ENV_DOC_GRAPH: &str = "FALKORDB_GRAPH_DOC";
const ENV_DOC_COLLECTION: &str = "QDRANT_COLLECTION_DOC";
const ENV_CODE_PROVIDER: [&str; 2] = ["CODE_GRAPH_PROVIDER", "GRAPH_PROVIDER"];
const ENV_DOC_PROVIDER: [&str; 2] = ["DOC_GRAPH_PROVIDER", "GRAPH_PROVIDER"];

/// `_default_config_dir` — locate `.cortext-harness/config` relative to CWD.
pub fn default_config_dir() -> PathBuf {
    if let Ok(explicit) = std::env::var("CORTEX_HARNESS_CONFIG_PATH") {
        let explicit = explicit.trim().to_string();
        if !explicit.is_empty() {
            let path = PathBuf::from(explicit);
            return if path.is_dir() {
                path
            } else {
                path.parent().map(Path::to_path_buf).unwrap_or(path)
            };
        }
    }
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let mut candidates = vec![cwd.clone()];
    candidates.extend(cwd.ancestors().skip(1).map(Path::to_path_buf));
    for candidate in candidates {
        let path = candidate.join(DEFAULT_CONFIG_DIRNAME);
        if path.is_dir() {
            return path;
        }
    }
    cwd.join(DEFAULT_CONFIG_DIRNAME)
}

fn read_config_files(config_dir: &Path) -> Vec<Value> {
    let Ok(entries) = std::fs::read_dir(config_dir) else {
        return Vec::new();
    };
    let mut paths: Vec<PathBuf> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.extension().and_then(|extension| extension.to_str()) == Some("json")
        })
        .collect();
    // Sorted by file name, mirroring `sorted(config_dir.glob("*.json"))`.
    paths.sort();
    let mut documents = Vec::new();
    for path in paths {
        let Ok(text) = std::fs::read_to_string(&path) else {
            continue;
        };
        if let Ok(payload) = serde_json::from_str::<Value>(&text)
            && payload.is_object()
        {
            documents.push(payload);
        }
    }
    documents
}

fn env_object(section: Option<&Value>) -> serde_json::Map<String, Value> {
    section
        .and_then(|section| section.get("env"))
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default()
}

fn string_field(object: &serde_json::Map<String, Value>, key: &str) -> Option<String> {
    object.get(key).and_then(Value::as_str).map(str::to_string)
}

/// One flattened project descriptor (`_project_entries` entries).
#[derive(Debug, Clone)]
struct ProjectEntry {
    project_id: String,
    parser_type: Option<String>,
    code_env: serde_json::Map<String, Value>,
    doc_env: serde_json::Map<String, Value>,
    #[allow(dead_code)]
    active: bool,
    storage_backend: String,
    remote_config: Option<Value>,
}

/// `_project_entries` — flatten the `[project]` / `[code, doc]` config shape.
fn project_entries(documents: &[Value]) -> Result<Vec<ProjectEntry>, ProjectRegistryError> {
    let mut entries = Vec::new();
    for document in documents {
        let Some(document_object) = document.as_object() else {
            continue;
        };
        let project_section = document_object
            .get("project")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        let Some(project_id) = string_field(&project_section, PROJECT_CODE_KEY)
            .filter(|value| !value.is_empty())
            .or_else(|| string_field(&project_section, PROJECT_NAME_KEY))
        else {
            continue;
        };
        let code_env = env_object(document_object.get("code"));
        let doc_env = env_object(document_object.get("doc"));
        let parser_type = string_field(&project_section, "parser_type")
            .or_else(|| string_field(&project_section, "parser"))
            .or_else(|| string_field(&code_env, "PARSER_TYPE"));
        entries.push(ProjectEntry {
            project_id,
            parser_type,
            code_env,
            doc_env,
            active: document_object.get("active").and_then(Value::as_bool).unwrap_or(false),
            storage_backend: document_object
                .get("storage_backend")
                .and_then(Value::as_str)
                .unwrap_or("local")
                .to_string(),
            remote_config: document_object
                .get("remote")
                .and_then(Value::as_object)
                .map(|object| Value::Object(object.clone())),
        });
    }
    let mut variants: std::collections::BTreeMap<String, Vec<String>> =
        std::collections::BTreeMap::new();
    for entry in &entries {
        if let Some(lookup) = project_id_lookup_key(Some(&entry.project_id)) {
            variants
                .entry(lookup)
                .or_default()
                .push(entry.project_id.clone());
        }
    }
    let collisions: Vec<(String, Vec<String>)> = variants
        .into_iter()
        .filter(|(_, values)| values.len() > 1)
        .map(|(key, mut values)| {
            values.sort();
            (key, values)
        })
        .collect();
    if !collisions.is_empty() {
        return Err(ProjectRegistryError::Duplicate(
            DuplicateProjectRegistrationError { collisions },
        ));
    }
    Ok(entries)
}

// ---------------------------------------------------------------------------
// Resolution
// ---------------------------------------------------------------------------

fn env_graph_key(provider: &str) -> &'static str {
    match provider {
        "neo4j" => ENV_NEO4J_CODE_GRAPH,
        "ladybug" => ENV_LADYBUG_CODE_GRAPH,
        _ => ENV_FALKOR_CODE_GRAPH,
    }
}

fn env_string(key: &str) -> Option<String> {
    std::env::var(key).ok().filter(|value| !value.is_empty())
}

/// `_resolve_targets` — build [`ProjectTargets`] for `project_id`.
#[allow(clippy::result_large_err)]
fn resolve_targets(
    project_id: Option<&str>,
    entries: &[ProjectEntry],
) -> Result<ProjectTargets, ProjectRegistryError> {
    let lookup = project_id_lookup_key(project_id);
    let Some(lookup) = lookup else {
        return Err(ProjectRegistryError::NotRegistered(
            ProjectNotRegisteredError::new(project_id, entry_ids(entries)),
        ));
    };

    let env_provider_value = first_env(&ENV_CODE_PROVIDER);
    let matched = entries.iter().find(|entry| {
        project_id_lookup_key(Some(&entry.project_id)).as_deref() == Some(lookup.as_str())
    });

    if matched.is_none() {
        let env_provider = normalize_graph_provider(env_provider_value.as_deref())
            .unwrap_or_else(|_| "falkordb".to_string());
        let env_graph = env_string(env_graph_key(&env_provider));
        let env_seeds = env_graph
            .or_else(|| env_string(ENV_CODE_COLLECTION))
            .or_else(|| env_string(ENV_DOC_COLLECTION))
            .or_else(|| first_env(&[ENV_DOC_GRAPH]))
            .or(env_provider_value.clone());
        if !entries.is_empty() {
            // A registry exists but this project isn't in it.
            return Err(ProjectRegistryError::NotRegistered(
                ProjectNotRegisteredError::new(project_id, entry_ids(entries)),
            ));
        }
        if env_seeds.is_none() {
            // Nothing to seed an ad-hoc project from.
            return Err(ProjectRegistryError::NotRegistered(
                ProjectNotRegisteredError::new(project_id, Vec::new()),
            ));
        }
    }

    // Canonical raw project_id: prefer the registered value so case variants
    // of the same logical project produce identical ProjectTargets.
    let canonical_project_id = matched
        .map(|entry| entry.project_id.clone())
        .or_else(|| project_id.map(str::to_string))
        .unwrap_or_default();

    let code_env = matched
        .map(|entry| entry.code_env.clone())
        .unwrap_or_default();
    let doc_env = matched
        .map(|entry| entry.doc_env.clone())
        .unwrap_or_default();
    // Only ad-hoc projects may use env vars.
    let env_allowed = matched.is_none();

    let provider = normalize_graph_provider(
        code_env
            .get("CODE_GRAPH_PROVIDER")
            .and_then(Value::as_str)
            .or_else(|| code_env.get("GRAPH_PROVIDER").and_then(Value::as_str))
            .or(if env_allowed {
                env_provider_value.as_deref()
            } else {
                None
            }),
    )
    .unwrap_or_else(|_| "falkordb".to_string());
    let doc_provider_env = if env_allowed {
        first_env(&ENV_DOC_PROVIDER)
    } else {
        None
    };
    let doc_provider = normalize_graph_provider(
        doc_env
            .get("DOC_GRAPH_PROVIDER")
            .and_then(Value::as_str)
            .or_else(|| doc_env.get("GRAPH_PROVIDER").and_then(Value::as_str))
            .or(doc_provider_env.as_deref())
            .or(Some(provider.as_str())),
    )
    .unwrap_or_else(|_| provider.clone());

    let code_graph_from_env = || {
        if env_allowed {
            env_string(env_graph_key(&provider))
        } else {
            None
        }
    };
    let code_graph = matched
        .and_then(|entry| string_field(&entry.code_env, env_graph_key(&provider)))
        .or_else(code_graph_from_env)
        .unwrap_or_else(|| canonical_project_id.clone());

    let code_qdrant_collection = matched
        .and_then(|entry| string_field(&entry.code_env, "QDRANT_COLLECTION"))
        .or_else(|| {
            if env_allowed {
                env_string(ENV_CODE_COLLECTION)
            } else {
                None
            }
        })
        .unwrap_or_else(|| canonical_project_id.clone());

    let doc_graph = matched
        .and_then(|entry| string_field(&entry.doc_env, env_graph_key(&doc_provider)))
        .or_else(|| {
            if env_allowed {
                env_string(ENV_DOC_GRAPH)
            } else {
                None
            }
        })
        .unwrap_or_else(|| format!("{canonical_project_id}_doc"));

    let doc_qdrant_collection = matched
        .and_then(|entry| string_field(&entry.doc_env, "QDRANT_COLLECTION"))
        .or_else(|| {
            if env_allowed {
                env_string(ENV_DOC_COLLECTION)
            } else {
                None
            }
        })
        .unwrap_or_else(|| format!("{canonical_project_id}_doc"));

    let parser_type = matched.and_then(|entry| entry.parser_type.clone());
    let storage_backend = matched
        .map(|entry| entry.storage_backend.clone())
        .unwrap_or_else(|| "local".to_string());
    let remote_config = matched.and_then(|entry| entry.remote_config.clone());

    Ok(ProjectTargets {
        project_id: canonical_project_id,
        project_id_normalized: lookup,
        code_graph,
        code_qdrant_collection,
        doc_graph,
        doc_qdrant_collection,
        parser_type,
        provider,
        storage_backend,
        remote_config,
        source: if matched.is_some() {
            "registry".to_string()
        } else {
            "env+defaults".to_string()
        },
    })
}

fn entry_ids(entries: &[ProjectEntry]) -> Vec<String> {
    entries.iter().map(|entry| entry.project_id.clone()).collect()
}

/// `resolve_project_targets` — resolve every storage target for `project_id`.
///
/// `None`, empty, and whitespace-only ids raise
/// [`ProjectRegistryError::NotRegistered`] with an empty known list.
#[allow(clippy::result_large_err)]
pub fn resolve_project_targets(
    project_id: Option<&str>,
    config_dir: Option<&Path>,
) -> Result<ProjectTargets, ProjectRegistryError> {
    let normalized = normalize_project_id(project_id);
    if normalized.is_none() {
        return Err(ProjectRegistryError::NotRegistered(
            ProjectNotRegisteredError::new(project_id, Vec::new()),
        ));
    }
    let directory: PathBuf = match config_dir {
        Some(directory) => directory.to_path_buf(),
        None => default_config_dir(),
    };
    let entries = project_entries(&read_config_files(&directory))?;
    resolve_targets(normalize_project_id(project_id).as_deref(), &entries)
}

/// `resolve_project_scope_candidates` — every registered project matching a
/// scoped query (exact case-insensitive match wins, else casefold prefix).
///
/// An empty result means the registry has no match at all; read-path callers
/// fall back to the raw id so out-of-band shards stay reachable. Unscoped
/// (`None`/blank) input yields `[]`.
pub fn resolve_project_scope_candidates(
    project_id: Option<&str>,
    config_dir: Option<&Path>,
) -> Result<Vec<ProjectTargets>, ProjectRegistryError> {
    let Some(query_key) = project_id_lookup_key(project_id) else {
        return Ok(Vec::new());
    };
    let directory: PathBuf = match config_dir {
        Some(directory) => directory.to_path_buf(),
        None => default_config_dir(),
    };
    let entries = project_entries(&read_config_files(&directory))?;
    let mut matches: Vec<&ProjectEntry> = entries
        .iter()
        .filter(|entry| {
            project_id_lookup_key(Some(&entry.project_id))
                .map(|candidate| candidate.starts_with(&query_key))
                .unwrap_or(false)
        })
        .collect();
    // Exact match (if present) collapses the result to that one project.
    let exact: Vec<&ProjectEntry> = matches
        .iter()
        .copied()
        .filter(|entry| {
            project_id_lookup_key(Some(&entry.project_id)).as_deref() == Some(query_key.as_str())
        })
        .collect();
    if !exact.is_empty() {
        matches = exact;
    }
    let mut resolved = Vec::new();
    for entry in &matches {
        resolved.push(resolve_targets(Some(&entry.project_id), &entries)?);
    }
    Ok(resolved)
}

/// `list_registered_projects` — raw `project_id` of every registered project,
/// in config-file order (deterministic).
pub fn list_registered_projects(config_dir: Option<&Path>) -> Result<Vec<String>, ProjectRegistryError> {
    let directory: PathBuf = match config_dir {
        Some(directory) => directory.to_path_buf(),
        None => default_config_dir(),
    };
    let entries = project_entries(&read_config_files(&directory))?;
    Ok(entry_ids(&entries))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_config(dir: &Path, file_name: &str, payload: Value) {
        std::fs::create_dir_all(dir).unwrap();
        let path = dir.join(file_name);
        std::fs::write(
            path,
            serde_json::to_string_pretty(&payload).unwrap(),
        )
        .unwrap();
    }

    #[test]
    fn unregistered_project_message_matches_python() {
        let error = ProjectNotRegisteredError::new(
            Some("nope"),
            vec!["cortext".to_string()],
        );
        assert_eq!(
            error.message(),
            "project_id 'nope' is not registered. Known projects: cortext. \
             Add a project section to .cortext-harness/config/*.json or pass \
             an explicit override."
        );
        let empty = ProjectNotRegisteredError::new(None, Vec::new());
        assert_eq!(
            empty.message(),
            "project_id 'None' is not registered. Known projects: <none registered>. \
             Add a project section to .cortext-harness/config/*.json or pass \
             an explicit override."
        );
    }

    #[test]
    fn resolves_targets_from_config_dir() {
        let temp = tempfile::tempdir().unwrap();
        write_config(
            temp.path(),
            "dev.json",
            serde_json::json!({
                "active": true,
                "project": {"code": "cortext", "name": "cortext"},
                "storage_backend": "local",
                "code": {"env": {
                    "GRAPH_PROVIDER": "falkordb",
                    "FALKORDB_GRAPH": "cortext",
                    "QDRANT_COLLECTION": "cortext"
                }},
                "doc": {"env": {"FALKORDB_GRAPH": "cortext_doc"}}
            }),
        );
        let targets = resolve_project_targets(Some("  Cortext "), Some(temp.path())).unwrap();
        assert_eq!(targets.project_id, "cortext");
        assert_eq!(targets.project_id_normalized, "cortext");
        assert_eq!(targets.code_graph, "cortext");
        assert_eq!(targets.code_qdrant_collection, "cortext");
        assert_eq!(targets.doc_graph, "cortext_doc");
        assert_eq!(targets.provider, "falkordb");
        assert_eq!(targets.source, "registry");

        // Naming-convention defaults apply when env is omitted.
        write_config(
            temp.path(),
            "bank.json",
            serde_json::json!({"active": false, "project": {"code": "bank"}}),
        );
        let bank = resolve_project_targets(Some("bank"), Some(temp.path())).unwrap();
        assert_eq!(bank.code_graph, "bank");
        assert_eq!(bank.doc_graph, "bank_doc");
        assert_eq!(bank.provider, "falkordb");

        let error = resolve_project_targets(Some("nope"), Some(temp.path())).unwrap_err();
        match error {
            ProjectRegistryError::NotRegistered(error) => {
                assert_eq!(error.sorted_known(), vec!["bank".to_string(), "cortext".to_string()]);
                assert!(error.message().contains("Known projects: bank, cortext."));
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    #[test]
    fn scope_candidates_prefix_rule() {
        let temp = tempfile::tempdir().unwrap();
        write_config(
            temp.path(),
            "a.json",
            serde_json::json!({"project": {"code": "bank_android"}}),
        );
        write_config(
            temp.path(),
            "b.json",
            serde_json::json!({"project": {"code": "bank_Cplus"}}),
        );
        write_config(
            temp.path(),
            "c.json",
            serde_json::json!({"project": {"code": "other"}}),
        );
        let candidates =
            resolve_project_scope_candidates(Some("bank"), Some(temp.path())).unwrap();
        let ids: Vec<String> = candidates
            .iter()
            .map(|targets| targets.project_id.clone())
            .collect();
        assert_eq!(ids, vec!["bank_android".to_string(), "bank_Cplus".to_string()]);
        // Unregistered query yields an empty candidate list (raw-id fallback).
        let candidates = resolve_project_scope_candidates(Some("zzz"), Some(temp.path())).unwrap();
        assert!(candidates.is_empty());
    }

    #[test]
    fn duplicate_registration_is_rejected() {
        let temp = tempfile::tempdir().unwrap();
        write_config(
            temp.path(),
            "a.json",
            serde_json::json!({"project": {"code": "Bank"}}),
        );
        write_config(
            temp.path(),
            "b.json",
            serde_json::json!({"project": {"code": "bank"}}),
        );
        let error = list_registered_projects(Some(temp.path())).unwrap_err();
        match error {
            ProjectRegistryError::Duplicate(error) => {
                assert!(error.message().starts_with(
                    "Duplicate project registrations after casefold normalization: bank: "
                ));
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    #[test]
    fn provider_aliases() {
        assert_eq!(normalize_graph_provider(Some("falkor")).unwrap(), "falkordb");
        assert_eq!(normalize_graph_provider(Some("Neo")).unwrap(), "neo4j");
        assert_eq!(normalize_graph_provider(Some("kuzu")).unwrap(), "ladybug");
        assert_eq!(normalize_graph_provider(None).unwrap(), "falkordb");
        assert!(normalize_graph_provider(Some("oracle")).is_err());
    }
}

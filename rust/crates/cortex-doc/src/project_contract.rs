//! Port of `doc-tiny/project_contract.py` — the project-registry contract
//! (`{project_id}_doc` naming convention + `.cortext-harness/config/*.json`
//! overrides), including the `CORTEX_HARNESS_CONFIG_PATH` escape hatch.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::Serialize;

pub const PROJECT_ID_NORMALIZED_FIELD: &str = "project_id_normalized";

/// Python `str.casefold()` approximation: full lowercase plus the hard fold
/// of sharp-s (the only casefold mapping that changes string length for the
/// Latin-1/ BMP range project ids use).
pub fn casefold(value: &str) -> String {
    value.to_lowercase().replace('ß', "ss")
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct ProjectTargets {
    pub project_id: String,
    pub project_id_normalized: String,
    pub doc_graph: String,
    pub doc_qdrant_collection: String,
}

#[derive(Debug)]
pub enum ContractError {
    NotRegistered {
        project_id: String,
        known: Vec<String>,
    },
    Duplicate {
        message: String,
    },
    Io(String),
}

impl std::fmt::Display for ContractError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ContractError::NotRegistered { project_id, known } => {
                let rendered = if known.is_empty() {
                    "<none registered>".to_string()
                } else {
                    known.join(", ")
                };
                write!(
                    f,
                    "project_id '{project_id}' is not registered. Known projects: {rendered}. \
                     Add a project section to .cortext-harness/config/*.json or pass an \
                     explicit override."
                )
            }
            ContractError::Duplicate { message } => write!(f, "{message}"),
            ContractError::Io(message) => write!(f, "{message}"),
        }
    }
}

impl std::error::Error for ContractError {}

/// `normalize_project_id`.
pub fn normalize_project_id(value: Option<&str>) -> Option<String> {
    let normalized = value?.trim().to_string();
    if normalized.is_empty() {
        None
    } else {
        Some(normalized)
    }
}

/// `project_id_lookup_key`.
pub fn project_id_lookup_key(value: Option<&str>) -> Option<String> {
    normalize_project_id(value).map(|v| casefold(&v))
}

/// `_default_config_dir` — explicit env path, else walk up from CWD looking
/// for `.cortext-harness/config`.
pub fn default_config_dir() -> PathBuf {
    if let Ok(explicit) = std::env::var("CORTEX_HARNESS_CONFIG_PATH") {
        let explicit = explicit.trim().to_string();
        if !explicit.is_empty() {
            let path = PathBuf::from(explicit);
            if path.is_dir() {
                return path;
            }
            return path
                .parent()
                .map(Path::to_path_buf)
                .unwrap_or_else(|| PathBuf::from("."));
        }
    }
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let mut candidate = Some(cwd.as_path());
    while let Some(dir) = candidate {
        let path = dir.join(".cortext-harness").join("config");
        if path.is_dir() {
            return path;
        }
        candidate = dir.parent();
    }
    cwd.join(".cortext-harness").join("config")
}

struct RawEntry {
    project_id: String,
    doc_env: BTreeMap<String, String>,
}

/// `_read_project_entries` — parse `*.json` config files sorted by name,
/// collecting `project.code` / `project.name` plus `doc.env` overrides.
fn read_project_entries(config_dir: &Path) -> Result<Vec<RawEntry>, ContractError> {
    let mut entries = Vec::new();
    let Ok(dir_entries) = std::fs::read_dir(config_dir) else {
        return Ok(entries);
    };
    let mut files: Vec<PathBuf> = dir_entries
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().map(|ext| ext == "json").unwrap_or(false))
        .collect();
    files.sort();
    for file_path in files {
        let Ok(content) = std::fs::read_to_string(&file_path) else {
            continue;
        };
        let Ok(payload) = serde_json::from_str::<serde_json::Value>(&content) else {
            continue;
        };
        let Some(project) = payload.get("project") else {
            continue;
        };
        let project_id = project
            .get("code")
            .and_then(serde_json::Value::as_str)
            .or_else(|| project.get("name").and_then(serde_json::Value::as_str));
        let Some(project_id) = project_id else {
            continue;
        };
        let mut doc_env = BTreeMap::new();
        if let Some(env_obj) = payload
            .get("doc")
            .and_then(|doc| doc.get("env"))
            .and_then(serde_json::Value::as_object)
        {
            for (key, value) in env_obj {
                let rendered = match value {
                    serde_json::Value::String(s) => s.clone(),
                    serde_json::Value::Null => continue,
                    other => other.to_string(),
                };
                doc_env.insert(key.clone(), rendered);
            }
        }
        entries.push(RawEntry {
            project_id: project_id.to_string(),
            doc_env,
        });
    }

    // Duplicate registration detection after casefold normalization.
    let mut variants: BTreeMap<String, Vec<String>> = BTreeMap::new();
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
        .filter(|(_, ids)| ids.len() > 1)
        .collect();
    if !collisions.is_empty() {
        let details = collisions
            .iter()
            .map(|(key, ids)| format!("{key}: {}", ids.join(", ")))
            .collect::<Vec<_>>()
            .join("; ");
        return Err(ContractError::Duplicate {
            message: format!(
                "Duplicate project registrations after casefold normalization: {details}."
            ),
        });
    }
    Ok(entries)
}

fn resolve_targets(
    project_id: &str,
    entries: &[RawEntry],
) -> Result<ProjectTargets, ContractError> {
    let lookup =
        project_id_lookup_key(Some(project_id)).ok_or_else(|| ContractError::NotRegistered {
            project_id: project_id.to_string(),
            known: entries.iter().map(|e| e.project_id.clone()).collect(),
        })?;
    let known = || {
        entries
            .iter()
            .map(|e| e.project_id.clone())
            .collect::<Vec<_>>()
    };
    let matched = entries
        .iter()
        .find(|entry| {
            project_id_lookup_key(Some(&entry.project_id)).as_deref() == Some(lookup.as_str())
        })
        .ok_or_else(|| ContractError::NotRegistered {
            project_id: project_id.to_string(),
            known: known(),
        })?;
    // Canonicalize to the registered spelling.
    let canonical_project_id = matched.project_id.clone();
    let doc_graph = matched
        .doc_env
        .get("FALKORDB_GRAPH")
        .or_else(|| matched.doc_env.get("NEO4J_DB"))
        .cloned()
        .unwrap_or_else(|| format!("{canonical_project_id}_doc"));
    let doc_qdrant_collection = matched
        .doc_env
        .get("QDRANT_COLLECTION")
        .cloned()
        .unwrap_or_else(|| format!("{canonical_project_id}_doc"));
    Ok(ProjectTargets {
        project_id: canonical_project_id,
        project_id_normalized: lookup,
        doc_graph,
        doc_qdrant_collection,
    })
}

/// `resolve_project_targets(project_id)`.
pub fn resolve_project_targets(
    project_id: &str,
    config_dir: Option<&Path>,
) -> Result<ProjectTargets, ContractError> {
    let Some(normalized) = normalize_project_id(Some(project_id)) else {
        return Err(ContractError::NotRegistered {
            project_id: project_id.to_string(),
            known: Vec::new(),
        });
    };
    let directory = config_dir
        .map(Path::to_path_buf)
        .unwrap_or_else(default_config_dir);
    let entries = read_project_entries(&directory)?;
    resolve_targets(&normalized, &entries)
}

/// `resolve_doc_candidates(project_id)` — prefix/LIKE rule over the registry.
pub fn resolve_doc_candidates(
    project_id: &str,
    config_dir: Option<&Path>,
) -> Result<Vec<ProjectTargets>, ContractError> {
    let Some(query_key) = project_id_lookup_key(Some(project_id)) else {
        return Ok(Vec::new());
    };
    let directory = config_dir
        .map(Path::to_path_buf)
        .unwrap_or_else(default_config_dir);
    let entries = read_project_entries(&directory)?;
    let mut matches: Vec<&RawEntry> = entries
        .iter()
        .filter(|entry| {
            project_id_lookup_key(Some(&entry.project_id))
                .map(|key| key.starts_with(&query_key))
                .unwrap_or(false)
        })
        .collect();
    let exact: Vec<&&RawEntry> = matches
        .iter()
        .filter(|entry| {
            project_id_lookup_key(Some(&entry.project_id)).as_deref() == Some(query_key.as_str())
        })
        .collect();
    if !exact.is_empty() {
        matches = exact.into_iter().copied().collect();
    }
    matches
        .iter()
        .map(|entry| resolve_targets(&entry.project_id, &entries))
        .collect()
}

/// `list_registered_projects`.
pub fn list_registered_projects(config_dir: Option<&Path>) -> Result<Vec<String>, ContractError> {
    let directory = config_dir
        .map(Path::to_path_buf)
        .unwrap_or_else(default_config_dir);
    Ok(read_project_entries(&directory)?
        .into_iter()
        .map(|entry| entry.project_id)
        .collect())
}

/// `qdrant_project_filter(project_id, known_ids)` — canonical Qdrant payload
/// filter (JSON shape) for a doc project scope.
pub fn qdrant_project_filter(
    project_id: Option<&str>,
    known_ids: Option<&[String]>,
) -> Option<serde_json::Value> {
    let keys = project_id_scope_keys(project_id, known_ids)?;
    Some(serde_json::json!({
        "must": [
            { "key": PROJECT_ID_NORMALIZED_FIELD, "match": { "any": keys } },
        ],
    }))
}

/// `project_id_scope_keys` — the query's own casefold key plus every known id
/// whose casefold key starts with it.
pub fn project_id_scope_keys(
    project_id: Option<&str>,
    known_ids: Option<&[String]>,
) -> Option<Vec<String>> {
    let query_key = project_id_lookup_key(project_id)?;
    let mut keys = vec![query_key.clone()];
    let known: Vec<String> = match known_ids {
        Some(ids) => ids.to_vec(),
        None => list_registered_projects(None).unwrap_or_default(),
    };
    for id in known {
        if let Some(known_key) = project_id_lookup_key(Some(&id))
            && known_key.starts_with(&query_key)
            && !keys.contains(&known_key)
        {
            keys.push(known_key);
        }
    }
    keys.sort();
    Some(keys)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_config(dir: &Path, name: &str, body: &str) {
        std::fs::create_dir_all(dir).unwrap();
        std::fs::write(dir.join(name), body).unwrap();
    }

    #[test]
    fn registry_resolution_and_naming_convention() {
        let tmp = tempfile::tempdir().unwrap();
        write_config(
            tmp.path(),
            "b.json",
            r#"{"project": {"code": "BankA"}, "doc": {"env": {"FALKORDB_GRAPH": "bank_doc"}}}"#,
        );
        write_config(tmp.path(), "a.json", r#"{"project": {"code": "p14doc"}}"#);

        let targets = resolve_project_targets("banka", Some(tmp.path())).unwrap();
        assert_eq!(targets.project_id, "BankA"); // canonical registered spelling
        assert_eq!(targets.project_id_normalized, "banka");
        assert_eq!(targets.doc_graph, "bank_doc"); // doc.env override wins
        assert_eq!(targets.doc_qdrant_collection, "BankA_doc"); // naming convention

        let targets = resolve_project_targets("p14doc", Some(tmp.path())).unwrap();
        assert_eq!(targets.doc_graph, "p14doc_doc");
        assert_eq!(targets.doc_qdrant_collection, "p14doc_doc");

        let list = list_registered_projects(Some(tmp.path())).unwrap();
        assert_eq!(list, vec!["p14doc", "BankA"]); // sorted by file name

        let err = resolve_project_targets("missing", Some(tmp.path())).unwrap_err();
        assert!(err.to_string().contains("is not registered"));
    }

    #[test]
    fn duplicate_registration_detected() {
        let tmp = tempfile::tempdir().unwrap();
        write_config(tmp.path(), "a.json", r#"{"project": {"code": "dup"}}"#);
        write_config(tmp.path(), "b.json", r#"{"project": {"code": "DUP"}}"#);
        let err = resolve_project_targets("dup", Some(tmp.path())).unwrap_err();
        assert!(err.to_string().contains("Duplicate project registrations"));
    }

    #[test]
    fn scope_keys_prefix_expansion() {
        let tmp = tempfile::tempdir().unwrap();
        write_config(
            tmp.path(),
            "a.json",
            r#"{"project": {"code": "bank_android"}}"#,
        );
        write_config(
            tmp.path(),
            "b.json",
            r#"{"project": {"code": "bank_Cplus"}}"#,
        );
        let known = list_registered_projects(Some(tmp.path())).unwrap();
        let keys = project_id_scope_keys(Some("BANK"), Some(&known)).unwrap();
        assert_eq!(keys, vec!["bank", "bank_android", "bank_cplus"]);
        assert!(project_id_scope_keys(None, None).is_none());
    }
}

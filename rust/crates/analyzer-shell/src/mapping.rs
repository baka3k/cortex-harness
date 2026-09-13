//! Port `tools/shell/mapping.py` — program mapping ledger loader.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::models::ProgramMapping;

/// `_canonical_source_path` — realpath hoá về rel-posix dưới root; path
/// chứa root-name prefix (vd `stock/bin/run.sh`) được strip prefix.
fn canonical_source_path(raw_path: &str, root_real: &Path) -> Result<String, String> {
    let expanded = expand_tilde(raw_path);
    let candidate = PathBuf::from(&expanded);
    let absolute = if candidate.is_absolute() {
        std::fs::canonicalize(&candidate).unwrap_or(candidate)
    } else {
        let normalized = raw_path.replace('\\', "/");
        let normalized = normalized.trim_start_matches(['.', '/']).to_string();
        let normalized = normalized.trim_start_matches('/').to_string();
        let root_name = root_real
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        let normalized = if normalized == root_name {
            String::new()
        } else if normalized.starts_with(&format!("{root_name}/")) {
            normalized[root_name.len() + 1..].to_string()
        } else {
            normalized
        };
        let joined = root_real.join(&normalized);
        std::fs::canonicalize(&joined).unwrap_or(joined)
    };
    if !absolute.starts_with(root_real) {
        return Err(format!("mapped source escapes project root: {raw_path}"));
    }
    Ok(cortex_analyzer_framework::scan::rel_posix(root_real, &absolute))
}

fn expand_tilde(path: &str) -> String {
    if let Some(rest) = path.strip_prefix("~/")
        && let Some(home) = std::env::var_os("HOME") {
            return PathBuf::from(home)
                .join(rest)
                .to_string_lossy()
                .to_string();
        }
    path.to_string()
}

/// `load_program_mappings` — ledger list/mappings-list, dedupe theo
/// program_id, conflict ⇒ Err.
pub fn load_program_mappings(
    path: &str,
    root_real: &Path,
    program_id_field: &str,
    source_path_field: &str,
    evidence_hash_field: &str,
) -> Result<Vec<ProgramMapping>, String> {
    let raw_payload = std::fs::read(path)
        .map_err(|error| format!("cannot read program mapping ledger: {error}"))?;
    let payload: serde_json::Value = serde_json::from_slice(&raw_payload)
        .map_err(|error| format!("cannot read program mapping ledger: {error}"))?;
    let ledger_hash = format!("sha256:{:x}", Sha256::digest(&raw_payload));
    let records: Vec<&serde_json::Map<String, serde_json::Value>> = match &payload {
        serde_json::Value::Array(items) => items
            .iter()
            .filter_map(|item| item.as_object())
            .collect(),
        serde_json::Value::Object(map) => match map.get("mappings") {
            Some(serde_json::Value::Array(items)) => items
                .iter()
                .filter_map(|item| item.as_object())
                .collect(),
            _ => return Err("program mapping ledger must be a list or contain a mappings list".into()),
        },
        _ => {
            return Err(
                "program mapping ledger must be a list or contain a mappings list".into(),
            )
        }
    };
    let mut mappings: BTreeMap<String, ProgramMapping> = BTreeMap::new();
    for (position, record) in records.iter().enumerate() {
        let program_id = record
            .get(program_id_field)
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();
        let source_path = record
            .get(source_path_field)
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .trim()
            .to_string();
        let evidence_hash = record
            .get(evidence_hash_field)
            .and_then(serde_json::Value::as_str)
            .map(str::to_string)
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| ledger_hash.clone())
            .trim()
            .to_string();
        if program_id.is_empty() || source_path.is_empty() {
            return Err(format!(
                "program mapping row requires configured program and source fields (row {position})"
            ));
        }
        let mapping = ProgramMapping {
            program_id: program_id.clone(),
            source_path: canonical_source_path(&source_path, root_real)?,
            evidence_hash,
        };
        if let Some(existing) = mappings.get(&program_id) {
            if existing != &mapping {
                return Err(format!("conflicting program mapping for {program_id:?}"));
            }
            continue;
        }
        mappings.insert(program_id, mapping);
    }
    Ok(mappings.into_values().collect())
}

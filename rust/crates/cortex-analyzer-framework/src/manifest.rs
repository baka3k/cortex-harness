//! Port `tools/common/git_diff.py::load_manifest_paths` — JSON dict
//! `{"files": [...]}`, JSON array, hoặc TXT lines; resolve về rel-posix
//! dưới root, bỏ path ngoài root.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

pub fn load_manifest_paths(path: &str, root: &Path) -> BTreeSet<String> {
    let text = match std::fs::read_to_string(path) {
        Ok(text) => text,
        Err(_) => return BTreeSet::new(),
    };
    let stripped = text.trim_start();
    let is_json = path.to_lowercase().ends_with(".json")
        || stripped.starts_with('{')
        || stripped.starts_with('[');
    let items: Vec<String> = if is_json {
        match serde_json::from_str::<serde_json::Value>(&text) {
            Ok(serde_json::Value::Object(map)) => match map.get("files") {
                Some(serde_json::Value::Array(items)) => strings_of(items),
                _ => Vec::new(),
            },
            Ok(serde_json::Value::Array(items)) => strings_of(&items),
            _ => Vec::new(),
        }
    } else {
        text.lines()
            .map(|line| line.trim().to_string())
            .filter(|line| !line.is_empty())
            .collect()
    };

    let root_abs = std::fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf());
    let mut resolved = BTreeSet::new();
    for raw in items {
        if raw.trim().is_empty() {
            continue;
        }
        let candidate_raw = PathBuf::from(raw.trim());
        let candidate = if candidate_raw.is_absolute() {
            candidate_raw
        } else {
            root_abs.join(candidate_raw)
        };
        let candidate = std::fs::canonicalize(&candidate).unwrap_or(candidate);
        if let Ok(rel) = candidate.strip_prefix(&root_abs) {
            resolved.insert(to_posix(rel));
        }
    }
    resolved
}

fn strings_of(items: &[serde_json::Value]) -> Vec<String> {
    items
        .iter()
        .filter_map(|item| item.as_str().map(str::to_string))
        .collect()
}

pub fn to_posix(path: &Path) -> String {
    path.components()
        .map(|c| c.as_os_str().to_string_lossy().to_string())
        .collect::<Vec<_>>()
        .join("/")
}

//! Port `tools/spring/cache.py` — fact artifact path + write.

use std::path::{Path, PathBuf};

use serde_json::Value;

use super::models::SPRING_PARSER_VERSION;
use super::pipeline::result_to_dict;
use crate::pyutil::{realpath, sha1_hex16};

/// `safe_cache_root` của `tools/common/analyzer_cache.py`.
pub fn safe_cache_root(cache_dir: Option<&str>, default_name: &str, project_root: &str) -> PathBuf {
    let base_root = match cache_dir {
        Some(dir) if !dir.is_empty() => dir.to_string(),
        _ => std::env::current_dir()
            .map(|cwd| cwd.join(".cache").to_string_lossy().to_string())
            .unwrap_or_else(|_| ".cache".to_string()),
    };
    let mut root = PathBuf::from(base_root).join(default_name);
    let normalized = realpath(Path::new(project_root));
    let basename = normalized
        .to_string_lossy()
        .trim_end_matches('/')
        .rsplit('/')
        .next()
        .unwrap_or("root")
        .to_string();
    let basename = if basename.is_empty() { "root".to_string() } else { basename };
    let digest = sha1_hex16(&normalized.to_string_lossy());
    root = root.join(format!("{}_{}", safe_segment(&basename), &digest[..12]));
    let _ = std::fs::create_dir_all(&root);
    root
}

/// `spring_cache_dir`.
pub fn spring_cache_dir(cache_dir: Option<&str>, root: &str, project_id: &str) -> PathBuf {
    safe_cache_root(cache_dir, "spring_facts", root).join(safe_segment(project_id))
}

/// `default_fact_artifact_path`.
pub fn default_fact_artifact_path(cache_dir: Option<&str>, root: &str, project_id: &str) -> PathBuf {
    spring_cache_dir(cache_dir, root, project_id).join("spring_facts.json")
}

/// `write_fact_artifact` — json.dump(payload, ensure_ascii=True, indent=2,
/// sort_keys=True) + "\n"; payload thêm `cache_version`.
pub fn write_fact_artifact(path: &Path, result: &super::models::SpringAnalysisResult) -> Result<(), String> {
    let mut payload = match result_to_dict(result) {
        Value::Object(map) => map,
        _ => serde_json::Map::new(),
    };
    payload.insert("cache_version".to_string(), Value::String(SPRING_PARSER_VERSION.to_string()));
    let value = Value::Object(payload);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let tmp_path = PathBuf::from(format!("{}.tmp", path.display()));
    let mut body = crate::pyjson::dumps_pretty(&value);
    if !body.ends_with('\n') {
        body.push('\n');
    }
    std::fs::write(&tmp_path, body).map_err(|error| error.to_string())?;
    std::fs::rename(&tmp_path, path).map_err(|error| error.to_string())?;
    Ok(())
}

/// `_safe_segment` — `[^A-Za-z0-9_.-]+` → `_`, strip "._", fallback "project".
pub fn safe_segment(value: &str) -> String {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| regex::Regex::new(r"[^A-Za-z0-9_.-]+").unwrap());
    let replaced = re.replace_all(value.trim(), "_").to_string();
    let cleaned = replaced.trim_matches(|c| c == '.' || c == '_');
    if cleaned.is_empty() {
        "project".to_string()
    } else {
        cleaned.to_string()
    }
}

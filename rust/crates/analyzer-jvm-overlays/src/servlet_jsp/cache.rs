//! Port `tools/servlet_jsp/cache.py` — preview/snapshot artifacts (checksum
//! envelope `payload_sha256`).

use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::servlet_jsp::models::{stable_digest, ResourceBudgets, ServletJspAnalysisResult, SERVLET_JSP_PARSER_VERSION};
use crate::pyutil::{realpath, sha256_hex};

pub const SNAPSHOT_SCHEMA_VERSION: i64 = 1;

/// `_safe_segment`.
pub fn safe_segment(value: &str) -> String {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| regex::Regex::new(r"[^A-Za-z0-9_.-]+").unwrap());
    let replaced = re.replace_all(value.trim(), "_").to_string();
    let cleaned = replaced.trim_matches(|c| c == '.' || c == '_');
    if cleaned.is_empty() {
        "value".to_string()
    } else {
        cleaned.to_string()
    }
}

/// `servlet_jsp_cache_dir`.
pub fn servlet_jsp_cache_dir(cache_dir: Option<&str>, root: &str, project_id: &str) -> PathBuf {
    let base = match cache_dir {
        Some(dir) if !dir.is_empty() => crate::pyutil::realpath(Path::new(dir)).to_string_lossy().to_string(),
        _ => {
            let xdg = std::env::var("XDG_CACHE_HOME").unwrap_or_else(|_| "~/.cache".to_string());
            let expanded = if let Some(rest) = xdg.strip_prefix("~/") {
                std::env::var("HOME").map(|home| format!("{home}/{rest}")).unwrap_or(xdg)
            } else {
                xdg
            };
            crate::pyutil::realpath(Path::new(&expanded))
                .join("hyper-graph")
                .to_string_lossy()
                .to_string()
        }
    };
    let root_digest = sha256_hex(realpath(Path::new(root)).to_string_lossy().as_bytes())[..16].to_string();
    let target = PathBuf::from(base)
        .join("servlet_jsp")
        .join(format!("{}-{}", safe_segment(project_id), root_digest));
    let _ = std::fs::create_dir_all(&target);
    target
}

pub fn preview_artifact_path(cache_dir: Option<&str>, root: &str, project_id: &str) -> PathBuf {
    servlet_jsp_cache_dir(cache_dir, root, project_id).join("servlet_jsp_preview.json")
}

pub fn generation_snapshot_path(
    cache_dir: Option<&str>,
    root: &str,
    project_id: &str,
    module_id: &str,
    generation_id: &str,
) -> PathBuf {
    let filename = format!("applied-{}-{}.json", safe_segment(module_id), safe_segment(generation_id));
    servlet_jsp_cache_dir(cache_dir, root, project_id).join(filename)
}

/// `_payload_checksum`.
pub fn payload_checksum(payload: &Value) -> String {
    sha256_hex(crate::pyjson::dumps_compact(payload).as_bytes())
}

pub fn generation_snapshot_payload(
    result: &ServletJspAnalysisResult,
    module_id: &str,
    generation_id: &str,
    budgets: &ResourceBudgets,
) -> Value {
    crate::pyjson::py_object(vec![
        ("artifact_role".into(), json!("graph_applied_generation")),
        ("schema_version".into(), json!(SNAPSHOT_SCHEMA_VERSION)),
        ("parser_version".into(), json!(SERVLET_JSP_PARSER_VERSION)),
        ("project_id".into(), json!(result.project_id)),
        (
            "project_root_digest".into(),
            json!(sha256_hex(realpath(Path::new(&result.root)).to_string_lossy().as_bytes())),
        ),
        ("module_id".into(), json!(module_id)),
        ("generation_id".into(), json!(generation_id)),
        ("budget_fingerprint".into(), json!(budgets.fingerprint())),
        ("result".into(), result.to_dict()),
    ])
}

pub fn generation_snapshot_checksum(
    result: &ServletJspAnalysisResult,
    module_id: &str,
    generation_id: &str,
    budgets: &ResourceBudgets,
) -> String {
    payload_checksum(&generation_snapshot_payload(result, module_id, generation_id, budgets))
}

/// `write_preview_artifact`.
pub fn write_preview_artifact(path: &Path, result: &ServletJspAnalysisResult) -> Result<String, String> {
    let payload = crate::pyjson::py_object(vec![
        ("artifact_role".into(), json!("preview")),
        ("schema_version".into(), json!(SNAPSHOT_SCHEMA_VERSION)),
        ("parser_version".into(), json!(SERVLET_JSP_PARSER_VERSION)),
        ("result".into(), result.to_dict()),
    ]);
    secure_atomic_json_write(path, &payload)
}

/// `write_generation_snapshot`.
pub fn write_generation_snapshot(
    path: &Path,
    result: &ServletJspAnalysisResult,
    module_id: &str,
    generation_id: &str,
    budgets: &ResourceBudgets,
) -> Result<String, String> {
    secure_atomic_json_write(
        path,
        &generation_snapshot_payload(result, module_id, generation_id, budgets),
    )
}

/// `secure_atomic_json_write` — envelope + checksum, indent 2 sort_keys
/// ensure_ascii + "\n", chmod 600.
pub fn secure_atomic_json_write(path: &Path, payload: &Value) -> Result<String, String> {
    let destination = crate::pyutil::abs_path(path);
    let parent = destination
        .parent()
        .map(|parent| parent.to_path_buf())
        .unwrap_or_else(|| PathBuf::from("."));
    std::fs::create_dir_all(&parent).map_err(|error| error.to_string())?;
    if destination.exists() {
        let metadata = std::fs::symlink_metadata(&destination).map_err(|error| error.to_string())?;
        if metadata.file_type().is_symlink() {
            return Err(format!("Refusing to replace symlinked output: {}", destination.display()));
        }
    }
    let checksum = payload_checksum(payload);
    let mut envelope = payload.as_object().cloned().unwrap_or_default();
    envelope.insert("payload_sha256".into(), Value::String(checksum.clone()));
    let body = crate::pyjson::dumps_pretty(&Value::Object(envelope));
    let tmp_path = parent.join(format!(
        ".{}.{}.tmp",
        destination.file_name().map(|n| n.to_string_lossy()).unwrap_or_default(),
        stable_digest(&[format!("{:?}", std::time::SystemTime::now())], 12)
    ));
    std::fs::write(&tmp_path, body).map_err(|error| error.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&tmp_path, std::fs::Permissions::from_mode(0o600));
    }
    std::fs::rename(&tmp_path, &destination).map_err(|error| error.to_string())?;
    Ok(checksum)
}

//! Structured JSON summary — schema orchestrator đọc sau analyzer run
//! (`--summary-path`); parity với `[SCAN_RESULT]` line.

use serde_json::{json, Value};

use crate::traits::AnalyzerResult;

/// Summary JSON: {parser, project_id, project_name, language, repo,
/// incremental, commit_sha, files, functions, classes, relations, calls,
/// written{label:count}, duration_seconds}.
#[allow(clippy::too_many_arguments)]
pub fn summary_json(
    result: &AnalyzerResult,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    incremental: bool,
    commit_sha_before: &str,
    commit_sha_after: &str,
) -> Value {
    json!({
        "parser": language,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "incremental": incremental,
        "commit_sha_before": commit_sha_before,
        "commit_sha": commit_sha_after,
        "files": result.files_scanned,
        "functions": result.functions,
        "classes": result.classes,
        "relations": result.relations,
        "calls": result.calls_resolved,
        "written": result.written,
        "duration_seconds": result.duration_seconds,
    })
}

/// `[SCAN_RESULT] parser=... files=... functions=... classes=...` — byte
/// giống Python `build_call_graph` print (flush=True).
pub fn scan_result_line(language: &str, files: usize, functions: usize, classes: usize) -> String {
    format!("[SCAN_RESULT] parser={language} files={files} functions={functions} classes={classes}")
}

pub fn write_summary(path: &str, value: &Value) -> Result<(), String> {
    let text = serde_json::to_string_pretty(value).map_err(|e| e.to_string())?;
    if let Some(parent) = std::path::Path::new(path).parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    std::fs::write(path, text + "\n").map_err(|e| e.to_string())
}

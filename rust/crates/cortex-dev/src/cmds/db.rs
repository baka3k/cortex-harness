//! `dev export-db / import-db / export / import` — delegate to
//! `cortex_harness.db_transfer` through the pyexec bridge (dev.py imports the
//! same functions in-process).

use crate::parser::Matches;
use crate::pyexec;
use std::path::Path;

pub fn export(m: &Matches, positional_project_id: Option<&str>) {
    let project_dir = m.value_or("--project-dir", ".");
    let args = serde_json::json!({
        "project_dir": Path::new(&project_dir).to_string_lossy(),
        "output": m.value("--output"),
        "project_id": m.value("--project-id").or(positional_project_id),
        "role": m.value_or("--role", "both"),
    });
    if let Err(out) = pyexec::try_call_json("db_export", &args) {
        // db_transfer errors are surfaced as `[error] ...` + exit 1.
        if !out.stderr.trim().is_empty() {
            eprint!("{}", out.stderr);
        }
        std::process::exit(if out.code == 0 { 1 } else { out.code });
    }
}

pub fn import(m: &Matches) {
    let project_dir = m.value_or("--project-dir", ".");
    let archive = m
        .value("--archive")
        .map(String::from)
        .or_else(|| m.positionals().first().cloned())
        .unwrap_or_default();
    let args = serde_json::json!({
        "project_dir": Path::new(&project_dir).to_string_lossy(),
        "archive": archive,
        "overwrite": m.flag("--overwrite"),
        "role": m.value_or("--role", "both"),
    });
    if let Err(out) = pyexec::try_call_json("db_import", &args) {
        if !out.stderr.trim().is_empty() {
            eprint!("{}", out.stderr);
        }
        std::process::exit(if out.code == 0 { 1 } else { out.code });
    }
}

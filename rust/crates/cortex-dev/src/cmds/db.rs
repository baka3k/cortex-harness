//! `dev export-db / import-db / export / import` — native `.cortexdb`
//! bundle export/import (the pyexec ops `db_export` / `db_import` retired
//! in phase-03; see `crate::db_transfer`).

use crate::db_transfer;
use crate::parser::Matches;
use std::path::Path;

pub fn export(m: &Matches, positional_project_id: Option<&str>) {
    let project_dir = m.value_or("--project-dir", ".");
    let outcome = db_transfer::export_project(
        Path::new(&project_dir),
        m.value("--output"),
        m.value("--project-id").or(positional_project_id),
        m.value_or("--role", "both").as_str(),
    );
    if let Err(e) = outcome {
        eprintln!("[error] {}", e);
        std::process::exit(1);
    }
}

pub fn import(m: &Matches) {
    let project_dir = m.value_or("--project-dir", ".");
    let archive = m
        .value("--archive")
        .map(String::from)
        .or_else(|| m.positionals().first().cloned())
        .unwrap_or_default();
    let outcome = db_transfer::import_project(
        Path::new(&project_dir),
        Path::new(&archive),
        m.flag("--overwrite"),
        m.value_or("--role", "both").as_str(),
    );
    if let Err(e) = outcome {
        eprintln!("[error] {}", e);
        std::process::exit(1);
    }
}

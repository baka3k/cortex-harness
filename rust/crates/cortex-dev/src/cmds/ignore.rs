//! `dev ignore add/remove/list` — user-configured ignore folders in the
//! active config.

use crate::config::{ignore_folders, load_active_config, save_config};
use crate::parser::Matches;
use crate::util::echo;

fn hint() {
    echo("[info] Changes apply to the next 'dev sync code' / 'dev sync doc' run.");
}

pub fn add(m: &Matches) {
    let project_path = super::init::resolve_path(std::path::Path::new(&project_dir(m)));
    let (mut cfg, cfg_path) = load_active_config(&project_path);

    let mut current = ignore_folders(&cfg);
    let mut added = Vec::new();
    let mut duplicate = Vec::new();
    for folder in m.positionals() {
        let name = folder.trim();
        if name.is_empty() {
            continue;
        }
        if current.iter().any(|c| c == name) {
            duplicate.push(name.to_string());
        } else {
            current.push(name.to_string());
            added.push(name.to_string());
        }
    }

    if let Some(obj) = cfg.as_object_mut() {
        obj.insert(
            "ignore".to_string(),
            serde_json::json!({ "folders": current }),
        );
    }
    save_config(&cfg, &cfg_path);
    for name in &added {
        echo(&format!("[ok] ignore add: {}", name));
    }
    for name in &duplicate {
        echo(&format!("[info] already ignored: {}", name));
    }
    hint();
}

pub fn remove(m: &Matches) {
    let project_path = super::init::resolve_path(std::path::Path::new(&project_dir(m)));
    let (mut cfg, cfg_path) = load_active_config(&project_path);

    let mut current = ignore_folders(&cfg);
    let mut removed = Vec::new();
    let mut missing = Vec::new();
    for folder in m.positionals() {
        let name = folder.trim();
        if let Some(pos) = current.iter().position(|c| c == name) {
            current.remove(pos);
            removed.push(name.to_string());
        } else {
            missing.push(name.to_string());
        }
    }

    if let Some(obj) = cfg.as_object_mut() {
        obj.insert(
            "ignore".to_string(),
            serde_json::json!({ "folders": current }),
        );
    }
    save_config(&cfg, &cfg_path);
    for name in &removed {
        echo(&format!("[ok] ignore remove: {}", name));
    }
    for name in &missing {
        echo(&format!("[warn] not in ignore list: {}", name));
    }
    hint();
}

pub fn list(m: &Matches) {
    let project_path = super::init::resolve_path(std::path::Path::new(&project_dir(m)));
    let (cfg, _) = load_active_config(&project_path);

    let entries = ignore_folders(&cfg);
    if entries.is_empty() {
        echo("No ignore folders configured.");
        return;
    }
    for entry in entries {
        echo(&entry);
    }
}

fn project_dir(m: &Matches) -> String {
    m.value_or("--project-dir", ".")
}

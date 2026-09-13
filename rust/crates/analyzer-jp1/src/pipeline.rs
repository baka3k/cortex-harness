//! JP1 analysis pipeline — port `tools/jp1/pipeline.py` (scan_jp1_files +
//! run_jp1_analysis) cùng các helper realpath/join theo `os.path` semantics.

use std::path::{Path, PathBuf};

use crate::parser::{decode_legacy_bytes, is_jp1_file, parse_jp1_text, Jp1File};

/// `_SKIP_DIRS` của jp1 pipeline (NHỎ hơn COMMON_SCAN_EXCLUDE — port đúng
/// từng chữ, KHÔNG dùng framework scan list).
pub const JP1_SKIP_DIRS: [&str; 6] = [".git", ".venv", "node_modules", "build", "dist", "target"];

pub struct Jp1AnalysisResult {
    pub project_id: String,
    pub files: Vec<Jp1File>,
    pub changed_paths: Vec<String>,
    pub deleted_paths: Vec<String>,
}

/// `os.path.realpath` — resolve symlink trên prefix TỒN TẠI dài nhất, giữ
/// phần không tồn tại (Python resolve(strict=False)).
pub fn py_realpath(path: &Path) -> PathBuf {
    if let Ok(resolved) = std::fs::canonicalize(path) {
        return resolved;
    }
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(path)
    };
    let normalized = normalize_path(&absolute);
    let mut suffix = PathBuf::new();
    let mut current = normalized.clone();
    loop {
        if let Ok(resolved) = std::fs::canonicalize(&current) {
            return resolved.join(&suffix);
        }
        match (current.parent(), current.file_name()) {
            (Some(parent), Some(name)) if parent != current => {
                suffix = PathBuf::from(name).join(suffix);
                current = parent.to_path_buf();
            }
            _ => break,
        }
    }
    normalized
}

fn normalize_path(path: &Path) -> PathBuf {
    let mut out = PathBuf::from("/");
    for component in path.components() {
        match component {
            std::path::Component::RootDir | std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

/// `Path(project_root, raw)` — join chuỗi (giữ `..`, không normalize).
pub fn join_py(root: &Path, rel: &str) -> PathBuf {
    if rel.is_empty() {
        root.to_path_buf()
    } else {
        root.join(rel)
    }
}

/// `scan_jp1_files` — os.walk với _SKIP_DIRS + matches_extra_ignore (env
/// CORTEX_EXTRA_IGNORE_DIRS), trả rel-posix paths SORTED.
pub fn scan_jp1_files(root: &Path) -> Vec<String> {
    let mut paths: Vec<String> = Vec::new();
    walk(root, root, &mut paths);
    paths.sort();
    paths
}

fn walk(root: &Path, dir: &Path, paths: &mut Vec<String>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let mut subdirs: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        if path.is_dir() {
            if !JP1_SKIP_DIRS.contains(&name.as_str())
                && !cortex_analyzer_framework::scan::matches_extra_ignore(&name)
            {
                subdirs.push(path);
            }
            continue;
        }
        let absolute = path.to_string_lossy().to_string();
        if is_jp1_file(&absolute) {
            let rel = path
                .strip_prefix(root)
                .map(|rel| {
                    rel.components()
                        .map(|c| c.as_os_str().to_string_lossy().to_string())
                        .collect::<Vec<_>>()
                        .join("/")
                })
                .unwrap_or_default();
            paths.push(rel);
        }
    }
    for sub in subdirs {
        walk(root, &sub, paths);
    }
}

/// `run_jp1_analysis`. UnicodeDecodeError-side errors ⇒ Err (Python lan lên
/// thành traceback rc != 0).
pub fn run_jp1_analysis(
    root: &Path,
    project_id: &str,
    changed_paths: Option<&[String]>,
    deleted_paths: &[String],
) -> Result<Jp1AnalysisResult, String> {
    let root_real = py_realpath(root);
    let selected: Vec<String> = match changed_paths {
        None => scan_jp1_files(&root_real),
        Some(changed) => {
            // Python: sorted(path.replace("\\", "/") for path in changed_paths
            //   if is_jp1_file(str(Path(root, path))))
            let mut selected: Vec<String> = changed
                .iter()
                .map(|path| path.replace('\\', "/"))
                .filter(|path| is_jp1_file(&join_py(&root_real, path).to_string_lossy()))
                .collect();
            selected.sort();
            selected
        }
    };
    let mut files: Vec<Jp1File> = Vec::new();
    for relative_path in &selected {
        let data = std::fs::read(join_py(&root_real, relative_path))
            .map_err(|e| format!("read {}: {e}", join_py(&root_real, relative_path).display()))?;
        let decoded = decode_legacy_bytes(&data).map_err(|_| "unicode decode error".to_string())?;
        let mut parsed = parse_jp1_text(&decoded.text, relative_path, &root_real);
        parsed.encoding = decoded.encoding;
        files.push(parsed);
    }
    Ok(Jp1AnalysisResult {
        project_id: project_id.to_string(),
        files,
        changed_paths: selected,
        deleted_paths: deleted_paths.to_vec(),
    })
}

/// `sorted(set(changed) | set(deleted))`.
pub fn cleanup_paths(changed: &[String], deleted: &[String]) -> Vec<String> {
    let mut set: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    set.extend(changed.iter().cloned());
    set.extend(deleted.iter().cloned());
    set.into_iter().collect()
}

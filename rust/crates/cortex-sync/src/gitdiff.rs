//! Port of `tools/common/git_diff.py` — git name-status diffing, worktree
//! collection, repository scope discovery, and manifest writing.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::json;

use crate::util;

#[derive(Debug, Clone)]
pub struct DiffEntry {
    pub status: String,
    pub old_path: Option<String>,
    pub new_path: Option<String>,
    pub source: String,
    pub repository_scope: String,
}

#[derive(Debug, Clone)]
pub struct RepositoryScope {
    pub source_prefix: String,
    pub root: String,
    pub git_root: String,
    pub git_pathspec: String,
}

pub fn to_posix(path: &str) -> String {
    path.replace('\\', "/")
}

fn git_bytes(root: &str, args: &[&str]) -> Result<Vec<u8>, (i32, String)> {
    let output = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .output()
        .map_err(|error| (-1, error.to_string()))?;
    if output.status.success() {
        Ok(output.stdout)
    } else {
        let mut text = output.stdout;
        text.extend_from_slice(&output.stderr);
        Err((output.status.code().unwrap_or(128), String::from_utf8_lossy(&text).to_string()))
    }
}

fn git_text(root: &str, args: &[&str]) -> Result<String, (i32, String)> {
    let bytes = git_bytes(root, args)?;
    Ok(decode_lossy(&bytes).trim().to_string())
}

/// `bytes.decode("utf-8", errors="surrogateescape")` equivalent — lossy but
/// byte-preserving for valid UTF-8 repositories.
pub fn decode_lossy(data: &[u8]) -> String {
    String::from_utf8_lossy(data).to_string()
}

/// `_repository_context` — (git_root, pathspec relative to git root).
pub fn repository_context(root: &str) -> Result<(String, String), (i32, String)> {
    let scan_root = crate::util::absolute(root);
    let toplevel = git_text(&util::path_to_string(&scan_root), &["rev-parse", "--show-toplevel"])?;
    let git_root = crate::util::realpath(&toplevel);
    let relative = scan_root.strip_prefix(&git_root);
    let relative = match relative.ok() {
        Some(rel) => rel,
        None => return Err((128, "git rev-parse failed".to_string())),
    };
    let relative_str = util::path_to_string(relative);
    Ok((
        util::path_to_string(&git_root),
        if relative_str == "." || relative_str.is_empty() {
            ".".to_string()
        } else {
            to_posix(&relative_str)
        },
    ))
}

/// `_parse_name_status_z` — parse `git diff --name-status -z` tokens.
fn parse_name_status_z(data: &[u8], source: &str, repository_scope: &str) -> Vec<DiffEntry> {
    let text = decode_lossy(data);
    let mut tokens: Vec<String> = text.split('\0').map(str::to_string).collect();
    if tokens.last().map(String::is_empty).unwrap_or(false) {
        tokens.pop();
    }
    let mut entries = Vec::new();
    let mut index = 0usize;
    while index < tokens.len() {
        let token = tokens[index].clone();
        index += 1;
        let (raw_status, first_path) = match token.split_once('\t') {
            Some((status, path)) => (status.to_string(), path.to_string()),
            None => {
                if index >= tokens.len() {
                    break;
                }
                let path = tokens[index].clone();
                index += 1;
                (token.clone(), path)
            }
        };
        let status = raw_status.chars().next().unwrap_or(' ').to_ascii_uppercase();
        match status {
            'R' | 'C' => {
                if index >= tokens.len() {
                    break;
                }
                let second_path = tokens[index].clone();
                index += 1;
                if status == 'R' {
                    entries.push(DiffEntry {
                        status: "R".to_string(),
                        old_path: Some(to_posix(&first_path)),
                        new_path: Some(to_posix(&second_path)),
                        source: source.to_string(),
                        repository_scope: repository_scope.to_string(),
                    });
                } else {
                    entries.push(DiffEntry {
                        status: "A".to_string(),
                        old_path: None,
                        new_path: Some(to_posix(&second_path)),
                        source: source.to_string(),
                        repository_scope: repository_scope.to_string(),
                    });
                }
            }
            'D' => entries.push(DiffEntry {
                status: "D".to_string(),
                old_path: Some(to_posix(&first_path)),
                new_path: None,
                source: source.to_string(),
                repository_scope: repository_scope.to_string(),
            }),
            'A' | 'M' | 'T' => entries.push(DiffEntry {
                status: if status == 'T' { "M".to_string() } else { status.to_string() },
                old_path: None,
                new_path: Some(to_posix(&first_path)),
                source: source.to_string(),
                repository_scope: repository_scope.to_string(),
            }),
            _ => {}
        }
    }
    entries
}

fn map_into_scan_root(entry: DiffEntry, pathspec: &str) -> Option<DiffEntry> {
    let prefix = if pathspec == "." {
        String::new()
    } else {
        format!("{}/", pathspec.trim_end_matches('/'))
    };
    let mapped = |path: &Option<String>| -> Option<String> {
        let raw = path.as_ref()?;
        let normalized = to_posix(raw);
        if !prefix.is_empty() && !normalized.starts_with(&prefix) {
            return None;
        }
        Some(if prefix.is_empty() { normalized } else { normalized[prefix.len()..].to_string() })
    };
    let old_path = mapped(&entry.old_path);
    let new_path = mapped(&entry.new_path);
    match entry.status.as_str() {
        "R" => match (old_path, new_path) {
            (Some(old), Some(new)) => Some(DiffEntry {
                status: "R".to_string(),
                old_path: Some(old),
                new_path: Some(new),
                source: entry.source,
                repository_scope: entry.repository_scope,
            }),
            (Some(old), None) => Some(DiffEntry {
                status: "D".to_string(),
                old_path: Some(old),
                new_path: None,
                source: entry.source,
                repository_scope: entry.repository_scope,
            }),
            (None, Some(new)) => Some(DiffEntry {
                status: "A".to_string(),
                old_path: None,
                new_path: Some(new),
                source: entry.source,
                repository_scope: entry.repository_scope,
            }),
            (None, None) => None,
        },
        "D" => old_path.map(|old| DiffEntry {
            status: "D".to_string(),
            old_path: Some(old),
            new_path: None,
            source: entry.source,
            repository_scope: entry.repository_scope,
        }),
        "A" | "M" => new_path.map(|new| DiffEntry {
            status: entry.status.clone(),
            old_path: None,
            new_path: Some(new),
            source: entry.source,
            repository_scope: entry.repository_scope,
        }),
        _ => None,
    }
}

fn collect_diff(root: &str, args: &[&str], source: &str) -> Result<Vec<DiffEntry>, (i32, String)> {
    let (git_root, pathspec) = repository_context(root)?;
    let data = git_bytes(
        &git_root,
        &[
            "diff",
            "--name-status",
            "-z",
            "--find-renames",
        ]
        .iter()
        .copied()
        .chain(args.iter().copied())
        .chain(["--", &pathspec])
        .collect::<Vec<_>>(),
    )?;
    let parsed = parse_name_status_z(&data, source, ".");
    Ok(parsed.into_iter().filter_map(|item| map_into_scan_root(item, &pathspec)).collect())
}

pub fn collect_git_diff_entries(root: &str, before_sha: &str, after_sha: &str) -> Result<Vec<DiffEntry>, (i32, String)> {
    collect_diff(root, &[before_sha, after_sha], "committed")
}

pub fn collect_worktree_entries(root: &str) -> Result<Vec<DiffEntry>, (i32, String)> {
    let mut entries = collect_diff(root, &["--cached", "HEAD"], "staged")?;
    entries.extend(collect_diff(root, &[], "unstaged")?);
    let (git_root, pathspec) = repository_context(root)?;
    let data = git_bytes(
        &git_root,
        &["ls-files", "-z", "--others", "--exclude-standard", "--", &pathspec],
    )?;
    let prefix = if pathspec == "." {
        String::new()
    } else {
        format!("{}/", pathspec.trim_end_matches('/'))
    };
    for raw_path in decode_lossy(&data).split('\0') {
        if raw_path.is_empty() {
            continue;
        }
        let normalized = to_posix(raw_path);
        if !prefix.is_empty() && !normalized.starts_with(&prefix) {
            continue;
        }
        let stripped = if prefix.is_empty() { normalized } else { normalized[prefix.len()..].to_string() };
        entries.push(DiffEntry {
            status: "A".to_string(),
            old_path: None,
            new_path: Some(stripped),
            source: "untracked".to_string(),
            repository_scope: ".".to_string(),
        });
    }
    Ok(entries)
}

fn submodule_paths(repo_root: &str) -> (Vec<String>, Option<serde_json::Value>) {
    let gitmodules = Path::new(repo_root).join(".gitmodules");
    if !gitmodules.is_file() {
        return (vec![], None);
    }
    let output = match git_text(repo_root, &["config", "--file", gitmodules.to_string_lossy().as_ref(), "--get-regexp", r"^submodule\..*\.path$"]) {
        Ok(text) => text,
        Err(_) => {
            return (
                vec![],
                Some(json!({"code": "gitmodules_unreadable", "path": gitmodules.to_string_lossy()})),
            )
        }
    };
    let mut results = Vec::new();
    for line in output.lines() {
        if let Some((_, path)) = line.split_once(char::is_whitespace) {
            results.push(path.trim().to_string());
        }
    }
    (results, None)
}

/// `discover_repository_scopes(root, recursive=True)`.
pub fn discover_repository_scopes(root: &str, recursive: bool) -> (Vec<RepositoryScope>, Vec<serde_json::Value>) {
    let scan_root = crate::util::absolute(root);
    let (git_root, pathspec) = match repository_context(&util::path_to_string(&scan_root)) {
        Ok(value) => value,
        Err(_) => {
            return (
                vec![],
                vec![json!({"code": "not_git_repository", "path": util::path_to_string(&scan_root)})],
            )
        }
    };
    let mut scopes = vec![RepositoryScope {
        source_prefix: ".".to_string(),
        root: util::path_to_string(&scan_root),
        git_root,
        git_pathspec: pathspec,
    }];
    let mut warnings = Vec::new();
    if !recursive {
        return (scopes, warnings);
    }
    #[cfg(windows)]
    fn normcase(value: &str) -> String {
        value.to_lowercase()
    }
    #[cfg(not(windows))]
    fn normcase(value: &str) -> String {
        value.to_string()
    }

    let mut queue = std::collections::VecDeque::new();
    queue.push_back(scopes[0].git_root.clone());
    let mut visited = std::collections::BTreeSet::new();
    visited.insert(normcase(&scopes[0].git_root));
    while let Some(parent) = queue.pop_front() {
        let (submodule_list, gitmodules_warning) = submodule_paths(&parent);
        if let Some(warning) = gitmodules_warning {
            warnings.push(warning);
        }
        for relative in submodule_list {
            let child = crate::util::realpath(
                &Path::new(&parent).join(&relative).to_string_lossy(),
            );
            let child_str = util::path_to_string(&child);
            let child_key = normcase(&child_str);
            // os.path.commonpath([scan_root, child]) == scan_root
            let scan_prefix = if scan_root.as_os_str().to_string_lossy().ends_with('/') {
                scan_root.to_string_lossy().to_string()
            } else {
                format!("{}/", scan_root.to_string_lossy())
            };
            let inside = child_str.starts_with(scan_prefix.as_str())
                || normcase(&util::path_to_string(&scan_root)) == child_key;
            if !inside {
                continue;
            }
            let prefix = match child.strip_prefix(&scan_root) {
                Ok(rel) => to_posix(&util::path_to_string(rel)),
                Err(_) => continue,
            };
            let git_marker = child.join(".git");
            if !(git_marker.is_dir() || git_marker.is_file()) {
                warnings.push(json!({"code": "submodule_uninitialized", "path": prefix}));
                continue;
            }
            if visited.contains(&child_key) {
                warnings.push(json!({"code": "submodule_cycle", "path": prefix}));
                continue;
            }
            let (child_git_root, _) = match repository_context(&child_str) {
                Ok(value) => value,
                Err(_) => {
                    warnings.push(json!({"code": "submodule_unreadable", "path": prefix}));
                    continue;
                }
            };
            if normcase(&child_git_root) != child_key {
                warnings.push(json!({"code": "submodule_unreadable", "path": prefix}));
                continue;
            }
            if git_text(&child_str, &["rev-parse", "HEAD"]).is_err() {
                warnings.push(json!({"code": "submodule_unreadable", "path": prefix}));
                continue;
            }
            visited.insert(child_key);
            scopes.push(RepositoryScope {
                source_prefix: prefix,
                root: child_str,
                git_root: child_git_root.clone(),
                git_pathspec: ".".to_string(),
            });
            queue.push_back(child_git_root.clone());
        }
    }
    scopes.sort_by(|a, b| {
        let a_root = a.source_prefix == ".";
        let b_root = b.source_prefix == ".";
        b_root.cmp(&a_root).then_with(|| a.source_prefix.cmp(&b.source_prefix))
    });
    (scopes, warnings)
}

/// `collect_changed_and_deleted`.
pub fn collect_changed_and_deleted(entries: &[DiffEntry]) -> (BTreeSet<String>, BTreeSet<String>) {
    let mut changed = BTreeSet::new();
    let mut deleted = BTreeSet::new();
    for entry in entries {
        match entry.status.as_str() {
            "A" | "M" => {
                if let Some(new) = &entry.new_path {
                    changed.insert(new.clone());
                }
            }
            "D" => {
                if let Some(old) = &entry.old_path {
                    deleted.insert(old.clone());
                }
            }
            "R" => {
                if let Some(old) = &entry.old_path {
                    deleted.insert(old.clone());
                }
                if let Some(new) = &entry.new_path {
                    changed.insert(new.clone());
                }
            }
            _ => {}
        }
    }
    (changed, deleted)
}

/// `write_manifest_paths` — `{"files": sorted}` with Python `indent=2` shape.
pub fn write_manifest_paths(path: &Path, files: &BTreeSet<String>) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let payload = json!({
        "files": files.iter().map(|f| to_posix(f)).collect::<Vec<_>>(),
    });
    let mut text = serde_json::to_string_pretty(&payload).unwrap();
    text.push('\n');
    std::fs::write(path, text)
}

/// `load_manifest_paths` — used by the parity harness; kept for symmetry.
#[allow(dead_code)]
pub fn load_manifest_paths(path: &Path, root: &Path) -> BTreeSet<String> {
    let text = match std::fs::read_to_string(path) {
        Ok(text) => text,
        Err(_) => return BTreeSet::new(),
    };
    let data: serde_json::Value = match serde_json::from_str(&text) {
        Ok(value) => value,
        Err(_) => return BTreeSet::new(),
    };
    let files = match data.get("files").and_then(|f| f.as_array()) {
        Some(files) => files.clone(),
        None => return BTreeSet::new(),
    };
    let root_abs = crate::util::realpath(&util::path_to_string(root));
    let mut resolved = BTreeSet::new();
    for raw in files {
        let raw = match raw.as_str() {
            Some(raw) => raw.trim().to_string(),
            None => continue,
        };
        if raw.is_empty() {
            continue;
        }
        let candidate = if Path::new(&raw).is_absolute() {
            crate::util::realpath(&raw)
        } else {
            crate::util::realpath(&root_abs.join(&raw).to_string_lossy())
        };
        if let Ok(rel) = candidate.strip_prefix(&root_abs) {
            resolved.insert(to_posix(&util::path_to_string(rel)));
        }
    }
    resolved
}

#[allow(dead_code)]
pub type PathMap = BTreeMap<String, String>;
#[allow(dead_code)]
pub type PathBufMap = BTreeMap<String, PathBuf>;

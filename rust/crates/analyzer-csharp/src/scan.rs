//! Port `csharp_analyzer.py` — `_scan_csharp_files` (`_SCAN_SKIP_DIRS` ∪
//! COMMON_SCAN_EXCLUDE ∪ extra-ignore), `_collect_csharp_import_graph` và
//! `_expand_impacted_files_by_imports` cho incremental selection.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use cortex_analyzer_framework::scan::{matches_extra_ignore, COMMON_SCAN_EXCLUDE};

/// `_SCAN_SKIP_DIRS` của csharp_analyzer.py (dirnames filter; các pattern
/// `*.dll`... không bao giờ khớp dirname nhưng giữ cho khớp Python).
pub const SCAN_SKIP_DIRS: [&str; 33] = [
    // Version control
    ".git", ".hg", ".svn",
    // IDE
    ".idea", ".vs", ".vscode", ".settings",
    // Build outputs (.NET)
    "bin", "obj", "Out", "out",
    // NuGet/packages
    "packages", ".nuget",
    // Build outputs (general)
    "build", "dist", "target",
    // Node (mixed projects)
    "node_modules",
    // Cache
    ".cache", ".parcel-cache", "__pycache__",
    // Testing
    "coverage", "TestResults", ".test-results", "test-results",
    // Temporary
    "tmp", "temp", ".tmp", "tmpdir",
    // OS specific
    ".DS_Store", "Thumbs.db",
    // Compiled/output files (file patterns — giữ trong set như Python)
    "*.dll", "*.exe", "*.pdb",
];

fn should_skip_dir(name: &str) -> bool {
    SCAN_SKIP_DIRS.contains(&name)
        || COMMON_SCAN_EXCLUDE.contains(&name)
        || matches_extra_ignore(name)
}

/// `_scan_csharp_files` — os.walk topdown với dirnames filter, sorted.
pub fn scan_csharp_files(root: &Path) -> Vec<PathBuf> {
    let mut files = BTreeSet::new();
    walk(root, &mut files);
    files.into_iter().collect()
}

fn walk(dir: &Path, files: &mut BTreeSet<PathBuf>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut subdirs: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        match entry.file_type() {
            Ok(file_type) if file_type.is_dir() => {
                if !should_skip_dir(&name) {
                    subdirs.push(path);
                }
            }
            Ok(_) => {
                if name.ends_with(".cs") {
                    files.insert(path);
                }
            }
            Err(_) => continue,
        }
    }
    for sub in subdirs {
        walk(&sub, files);
    }
}

/// rel-path posix từ root (khớp `os.path.relpath(...).replace("\\", "/")`).
pub fn rel_posix(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .components()
        .map(|component| component.as_os_str().to_string_lossy().to_string())
        .collect::<Vec<_>>()
        .join("/")
}

/// `_extract_csharp_namespace_and_usings_from_text`.
#[allow(dead_code)] // phase-03: chưa wire — giữ cho wiring composition/message lane sau
fn extract_namespace_and_usings(text: &str) -> (Option<String>, Vec<String>) {
    let mut namespace_name: Option<String> = None;
    let mut usings: Vec<String> = Vec::new();
    for raw_line in text.lines() {
        let line = raw_line.trim();
        if line.is_empty()
            || line.starts_with("//")
            || line.starts_with("/*")
            || line.starts_with('*')
        {
            continue;
        }
        if namespace_name.is_none()
            && let Some(name) = match_namespace(line) {
                namespace_name = Some(name);
                continue;
            }
        if let Some(name) = match_using(line) {
            usings.push(name);
        }
    }
    (namespace_name, usings)
}

/// `^namespace\s+([A-Za-z_][A-Za-z0-9_\.]*)` — namespace name có thể đứng
/// riêng trên một dòng (brace `{` ở dòng sau) — khớp test `namespace App.Controllers\n{`.
#[allow(dead_code)] // phase-03: chưa wire — giữ cho wiring composition/message lane sau
fn match_namespace(line: &str) -> Option<String> {
    let rest = line.strip_prefix("namespace")?;
    if !rest.starts_with(|c: char| c.is_whitespace()) {
        return None;
    }
    let rest = rest.trim_start();
    let (name, after) = parse_qualified_name(rest)?;
    let after = after.trim_start();
    // `;` hoặc `{` ngay sau, hoặc EOF dòng (brace ở dòng tiếp theo).
    if after.is_empty() || after.starts_with(';') || after.starts_with('{') {
        Some(name)
    } else {
        None
    }
}

/// `^using\s+(?:static\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\s*=\s*)?([A-Za-z_][A-Za-z0-9_\.]*)\s*;`
#[allow(dead_code)] // phase-03: chưa wire — giữ cho wiring composition/message lane sau
fn match_using(line: &str) -> Option<String> {
    let rest = line.strip_prefix("using")?;
    if !rest.starts_with(|c: char| c.is_whitespace()) {
        return None;
    }
    let mut rest = rest.trim_start();
    if let Some(stripped) = rest.strip_prefix("static")
        && stripped.starts_with(|c: char| c.is_whitespace()) {
            rest = stripped.trim_start();
        }
    // Optional alias `[A-Za-z_][A-Za-z0-9_]*\s*=\s*` (không có dấu chấm).
    if let Some(position) = find_alias_end(rest) {
        rest = rest[position..].trim_start();
    }
    let (name, after) = parse_qualified_name(rest)?;
    if after.trim_start().starts_with(';') {
        Some(name)
    } else {
        None
    }
}

/// Bắt `[A-Za-z_][A-Za-z0-9_\.]*` ở đầu `text`, trả (name, phần còn lại).
#[allow(dead_code)] // phase-03: chưa wire — giữ cho wiring composition/message lane sau
fn parse_qualified_name(text: &str) -> Option<(String, &str)> {
    let mut name = String::new();
    for (index, character) in text.char_indices() {
        let valid = if name.is_empty() {
            character.is_ascii_alphabetic() || character == '_'
        } else {
            character.is_ascii_alphanumeric() || character == '_' || character == '.'
        };
        if valid {
            name.push(character);
            continue;
        }
        if name.is_empty() {
            return None;
        }
        return Some((name, &text[index..]));
    }
    if name.is_empty() {
        None
    } else {
        Some((name, ""))
    }
}

/// Tìm vị trí sau alias `word\s*=\s*` (alias: `[A-Za-z_][A-Za-z0-9_]*`) nếu
/// pattern khớp ngay đầu text; None khi không có alias.
#[allow(dead_code)] // phase-03: chưa wire — giữ cho wiring composition/message lane sau
fn find_alias_end(text: &str) -> Option<usize> {
    let mut position = 0;
    let bytes = text.as_bytes();
    if position >= bytes.len() || !(bytes[position].is_ascii_alphabetic() || bytes[position] == b'_')
    {
        return None;
    }
    while position < bytes.len()
        && (bytes[position].is_ascii_alphanumeric() || bytes[position] == b'_')
    {
        position += 1;
    }
    let mut rest = &text[position..];
    let before = rest.len();
    rest = rest.trim_start();
    let mut after_trim = position + (before - rest.len());
    if !rest.starts_with('=') {
        return None;
    }
    after_trim += 1;
    let rest_after_equals = &text[after_trim..];
    let trimmed = rest_after_equals.trim_start();
    Some(text.len() - trimmed.len())
}

/// `_collect_csharp_import_graph` — namespace/use dependency theo rel-path.
#[allow(dead_code)] // phase-03: chưa wire — giữ cho wiring composition/message lane sau
fn collect_import_graph(files: &[PathBuf], root: &Path) -> BTreeMap<String, Vec<String>> {
    let mut namespace_to_files: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut namespace_by_file: BTreeMap<String, Option<String>> = BTreeMap::new();
    let mut using_by_file: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut rel_paths: Vec<String> = Vec::new();

    for abs_path in files {
        let rel_path = rel_posix(root, abs_path);
        rel_paths.push(rel_path.clone());
        let text = match std::fs::read_to_string(abs_path) {
            Ok(text) => text,
            Err(_) => {
                namespace_by_file.insert(rel_path.clone(), None);
                using_by_file.insert(rel_path, Vec::new());
                continue;
            }
        };
        let (namespace_name, usings) = extract_namespace_and_usings(&text);
        namespace_by_file.insert(rel_path.clone(), namespace_name.clone());
        using_by_file.insert(rel_path.clone(), usings.clone());
        if let Some(name) = namespace_name {
            namespace_to_files.entry(name).or_default().insert(rel_path);
        }
    }

    let mut deps_by_file: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for rel_path in &rel_paths {
        let mut resolved: BTreeSet<String> = BTreeSet::new();
        if let Some(Some(namespace_name)) = namespace_by_file.get(rel_path)
            && let Some(files_in_ns) = namespace_to_files.get(namespace_name) {
                resolved.extend(files_in_ns.iter().cloned());
            }
        for using in &using_by_file[rel_path] {
            if let Some(files_in_ns) = namespace_to_files.get(using) {
                resolved.extend(files_in_ns.iter().cloned());
            }
            if let Some(position) = using.rfind('.')
                && let Some(files_in_ns) = namespace_to_files.get(&using[..position]) {
                    resolved.extend(files_in_ns.iter().cloned());
                }
        }
        resolved.remove(rel_path);
        deps_by_file.insert(rel_path.clone(), resolved.into_iter().collect());
    }
    deps_by_file
}

/// `_expand_impacted_files_by_imports` — BFS trên reverse dependency graph.
#[allow(dead_code)] // phase-03: chưa wire — giữ cho wiring composition/message lane sau
fn expand_impacted_files(
    changed_existing: &BTreeSet<String>,
    deps_by_file: &BTreeMap<String, Vec<String>>,
) -> BTreeSet<String> {
    let mut reverse_map: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for (source, deps) in deps_by_file {
        for dep in deps {
            reverse_map.entry(dep.clone()).or_default().insert(source.clone());
        }
    }
    let mut impacted: BTreeSet<String> = BTreeSet::new();
    let mut queue: std::collections::VecDeque<String> = changed_existing.iter().cloned().collect();
    let mut seen: BTreeSet<String> = changed_existing.clone();
    while let Some(current) = queue.pop_front() {
        for dependent in reverse_map.get(&current).into_iter().flatten() {
            if seen.contains(dependent) {
                continue;
            }
            seen.insert(dependent.clone());
            impacted.insert(dependent.clone());
            queue.push_back(dependent.clone());
        }
    }
    impacted
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scan_skips_bin_obj_and_collects_cs_sorted() {
        let temp = tempfile::tempdir().expect("tempdir");
        let root = temp.path();
        std::fs::create_dir_all(root.join("bin")).unwrap();
        std::fs::create_dir_all(root.join("obj/Debug")).unwrap();
        std::fs::create_dir_all(root.join("Controllers")).unwrap();
        std::fs::create_dir_all(root.join("packages/X/lib")).unwrap();
        std::fs::write(root.join("zoo.cs"), "class Zoo {}").unwrap();
        std::fs::write(root.join("bin/skip.cs"), "class Skip {}").unwrap();
        std::fs::write(root.join("obj/skip2.cs"), "class Skip2 {}").unwrap();
        std::fs::write(root.join("Controllers/home.cs"), "class Home {}").unwrap();
        std::fs::write(root.join("packages/X/lib/pkg.cs"), "class Pkg {}").unwrap();
        std::fs::write(root.join("notes.txt"), "nope").unwrap();
        let scanned = scan_csharp_files(root);
        let rels: Vec<String> = scanned.iter().map(|path| rel_posix(root, path)).collect();
        assert_eq!(rels, vec!["Controllers/home.cs", "zoo.cs"]);
    }

    #[test]
    fn namespace_and_using_extraction() {
        let text = r#"
// comment
using System;
using System.Collections.Generic;
using static System.Math;
using Alias = System.Text;
using System.Linq;

namespace App.Controllers
{
    public class Home { }
}
"#;
        let (namespace_name, usings) = extract_namespace_and_usings(text);
        assert_eq!(namespace_name.as_deref(), Some("App.Controllers"));
        assert_eq!(
            usings,
            vec![
                "System",
                "System.Collections.Generic",
                "System.Math",
                "System.Text",
                "System.Linq",
            ]
        );
    }

    #[test]
    fn impacted_expansion_follows_reverse_deps() {
        // B uses namespace A → A changed ⇒ B impacted.
        let temp = tempfile::tempdir().expect("tempdir");
        let root = temp.path();
        std::fs::write(
            root.join("a.cs"),
            "namespace NS1\npublic class A {}\n",
        )
        .unwrap();
        std::fs::write(
            root.join("b.cs"),
            "using NS1;\nnamespace NS2\npublic class B { }\n",
        )
        .unwrap();
        std::fs::write(root.join("c.cs"), "namespace NS3\npublic class C { }\n").unwrap();
        let files = scan_csharp_files(root);
        let deps = collect_import_graph(&files, root);
        let changed: BTreeSet<String> = ["a.cs".to_string()].into_iter().collect();
        let impacted = expand_impacted_files(&changed, &deps);
        assert_eq!(impacted, BTreeSet::from(["b.cs".to_string()]));
    }
}

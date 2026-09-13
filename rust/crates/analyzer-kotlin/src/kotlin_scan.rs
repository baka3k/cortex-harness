//! Port `_scan_kotlin_files` + `_should_ignore_directory` +
//! `_collect_kotlin_import_graph` + `_expand_impacted_files_by_imports` của
//! kotlin_analyzer.py.
//!
//! Kotlin analyzer CÓ skip-list riêng (exact-match dirs + suffix list) union
//! `COMMON_SCAN_EXCLUDE` — KHÔNG dùng `scan::scan_files` của framework (list
//! đó là python-specific). File-level filter riêng: bỏ .class/.kotlin_module/
//! .swp/.swo, giữ .kt/.kts.

use std::collections::{BTreeSet, HashMap, HashSet, VecDeque};
use std::path::{Path, PathBuf};

use cortex_analyzer_framework::scan::{matches_extra_ignore, COMMON_SCAN_EXCLUDE};

/// Ignore set riêng của `_should_ignore_directory` (exact match) — union
/// COMMON_SCAN_EXCLUDE ở check-time như Python `| COMMON_SCAN_EXCLUDE`.
const KOTLIN_IGNORE_DIRS: [&str; 32] = [
    // Build outputs
    "target",
    "build",
    "out",
    "bin",
    "buildSrc",
    // Gradle/Maven cache
    ".gradle",
    ".mvn",
    "gradleCache",
    "caches",
    // IDE
    ".idea",
    ".vscode",
    ".settings",
    ".eclipse",
    // Version control
    ".git",
    ".svn",
    ".hg",
    // Temporary
    "tmp",
    "temp",
    ".tmp",
    "tmpdir",
    // Node (mixed projects)
    "node_modules",
    // Cache
    ".cache",
    ".parcel-cache",
    "__pycache__",
    // Testing
    "coverage",
    ".test-results",
    "junit",
    "test-results",
    // OS specific
    ".DS_Store",
    "Thumbs.db",
    // Misc
    ".project",
    ".classpath",
];

/// `_should_ignore_directory` — exact list | COMMON_SCAN_EXCLUDE | suffix list
/// | extra-ignore env.
pub fn should_ignore_directory(dir_name: &str) -> bool {
    if KOTLIN_IGNORE_DIRS.contains(&dir_name) || COMMON_SCAN_EXCLUDE.contains(&dir_name) {
        return true;
    }
    if dir_name.ends_with(".swp")
        || dir_name.ends_with(".swo")
        || dir_name.ends_with(".iml")
        || dir_name.ends_with(".ipr")
        || dir_name.ends_with(".iws")
    {
        return true;
    }
    matches_extra_ignore(dir_name)
}

/// `_scan_kotlin_files` — walk root (topdown, lọc dirs trước), thu .kt/.kts,
/// sort theo full path str.
pub fn scan_kotlin_files(root: &Path) -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = Vec::new();
    walk(root, &mut files);
    files.sort_by_key(|p| p.to_string_lossy().to_string());
    files
}

fn walk(dir: &Path, files: &mut Vec<PathBuf>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut subdirs: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        match entry.file_type() {
            Ok(t) if t.is_dir() => {
                if !should_ignore_directory(&name) && !matches_extra_ignore(&name) {
                    subdirs.push(path);
                }
            }
            Ok(_) => {
                if name.ends_with(".class")
                    || name.ends_with(".kotlin_module")
                    || name.ends_with(".swp")
                    || name.ends_with(".swo")
                {
                    continue;
                }
                if name == ".DS_Store" || name == "Thumbs.db" {
                    continue;
                }
                if name.ends_with(".kt") || name.ends_with(".kts") {
                    files.push(path);
                }
            }
            Err(_) => continue,
        }
    }
    for sub in subdirs {
        walk(&sub, files);
    }
}

/// Python `str.splitlines()` — nhiều boundary hơn '\n'.
fn py_splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0usize;
    let bytes = text.as_bytes();
    let mut i = 0usize;
    while i < text.len() {
        let c = text[i..].chars().next().unwrap();
        let boundary = matches!(
            c,
            '\n' | '\r' | '\u{0B}' | '\u{0C}' | '\u{1C}' | '\u{1D}' | '\u{1E}' | '\u{85}'
                | '\u{2028}' | '\u{2029}'
        );
        if boundary {
            let end = i;
            if c == '\r' && i + 1 < bytes.len() && bytes[i + 1] == b'\n' {
                i += 1;
            }
            lines.push(&text[start..end]);
            start = i + 1;
        }
        i += c.len_utf8();
    }
    if start < text.len() {
        lines.push(&text[start..]);
    } else if start == text.len() && text.is_empty() {
        lines.push("");
    }
    lines
}

/// `_extract_kotlin_package_and_imports_from_text` — regex từng dòng (bỏ
/// dòng trống / `//`), package chỉ nhận lần đầu, import có thể `as alias`.
pub fn extract_package_and_imports_from_text(text: &str) -> (Option<String>, Vec<String>) {
    let package_re =
        regex::Regex::new(r"^package\s+([A-Za-z_][A-Za-z0-9_\.]*)\s*$").expect("pkg re");
    let import_re = regex::Regex::new(
        r"^import\s+([A-Za-z_][A-Za-z0-9_\.]*)(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?\s*$",
    )
    .expect("import re");
    let mut package_name: Option<String> = None;
    let mut imports: Vec<String> = Vec::new();
    for raw_line in py_splitlines(text) {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with("//") {
            continue;
        }
        if package_name.is_none()
            && let Some(caps) = package_re.captures(line)
        {
            package_name = Some(caps.get(1).expect("group").as_str().to_string());
            continue;
        }
        if let Some(caps) = import_re.captures(line) {
            imports.push(caps.get(1).expect("group").as_str().to_string());
        }
    }
    (package_name, imports)
}

/// `_collect_kotlin_import_graph` — rel_path → sorted deps (rel_path của các
/// file cùng package với import prefix-match `imp == pkg || imp.starts_with(pkg + ".")`).
pub fn collect_kotlin_import_graph(
    all_kotlin_files: &[PathBuf],
    root: &Path,
) -> HashMap<String, Vec<String>> {
    let mut package_to_files: HashMap<String, BTreeSet<String>> = HashMap::new();
    let mut imports_by_file: HashMap<String, Vec<String>> = HashMap::new();
    let mut rel_paths: Vec<String> = Vec::new();

    for abs_path in all_kotlin_files {
        let rel_path = cortex_analyzer_framework::scan::rel_posix(root, abs_path);
        rel_paths.push(rel_path.clone());
        // Python mở với encoding="utf-8", errors="ignore"; OSError → imports=[].
        let text = match std::fs::read(abs_path) {
            Ok(bytes) => cortex_analyzer_framework::ts::decode_ignore(&bytes),
            Err(_) => {
                imports_by_file.insert(rel_path, Vec::new());
                continue;
            }
        };
        let (package_name, imports) = extract_package_and_imports_from_text(&text);
        imports_by_file.insert(rel_path.clone(), imports);
        if let Some(package_name) = package_name {
            package_to_files
                .entry(package_name)
                .or_default()
                .insert(rel_path);
        }
    }

    let mut deps_by_file: HashMap<String, Vec<String>> = HashMap::new();
    for rel_path in &rel_paths {
        let mut resolved: BTreeSet<String> = BTreeSet::new();
        for imp in imports_by_file.get(rel_path).into_iter().flatten() {
            for (package_name, files) in &package_to_files {
                if imp == package_name || imp.starts_with(format!("{package_name}.").as_str()) {
                    resolved.extend(files.iter().cloned());
                }
            }
        }
        resolved.remove(rel_path);
        deps_by_file.insert(rel_path.clone(), resolved.into_iter().collect());
    }
    deps_by_file
}

/// `_expand_impacted_files_by_imports` — BFS FIFO qua reverse deps, dependents
/// sorted mỗi bước.
pub fn expand_impacted_files_by_imports(
    changed_existing: &HashSet<String>,
    deps_by_file: &HashMap<String, Vec<String>>,
) -> HashSet<String> {
    let mut reverse_map: HashMap<String, Vec<String>> = HashMap::new();
    for (source, deps) in deps_by_file {
        for dep in deps {
            reverse_map.entry(dep.clone()).or_default().push(source.clone());
        }
    }

    let mut impacted: HashSet<String> = HashSet::new();
    let mut queue: VecDeque<String> = changed_existing.iter().cloned().collect();
    let mut seen: HashSet<String> = changed_existing.clone();
    while let Some(current) = queue.pop_front() {
        let mut dependents = reverse_map.get(&current).cloned().unwrap_or_default();
        dependents.sort();
        for dependent in dependents {
            if seen.contains(&dependent) {
                continue;
            }
            seen.insert(dependent.clone());
            impacted.insert(dependent.clone());
            queue.push_back(dependent);
        }
    }
    impacted
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ignore_dirs_match_kotlin_list() {
        assert!(should_ignore_directory("build"));
        assert!(should_ignore_directory("buildSrc"));
        assert!(should_ignore_directory("gradleCache"));
        assert!(should_ignore_directory("notes.iml"));
        assert!(should_ignore_directory(".gradle"));
        assert!(!should_ignore_directory("src"));
        assert!(!should_ignore_directory("geometry"));
    }

    #[test]
    fn package_import_extraction() {
        let text = "// lead\npackage com.demo.app\n\nimport com.example.geom.Circle\nimport x.Y as Z\nimport not a match\n";
        let (pkg, imports) = extract_package_and_imports_from_text(text);
        assert_eq!(pkg.as_deref(), Some("com.demo.app"));
        assert_eq!(imports, vec!["com.example.geom.Circle", "x.Y"]);
    }

    #[test]
    fn import_graph_prefix_match() {
        let tmp = std::env::temp_dir().join(format!("p06_kt_scan_test_{}", std::process::id()));
        let pkg_dir = tmp.join("src/com/example/geom");
        std::fs::create_dir_all(&pkg_dir).unwrap();
        std::fs::write(pkg_dir.join("Shape.kt"), b"package com.example.geom\n").unwrap();
        std::fs::write(
            tmp.join("src/Runner.kt"),
            b"package com.example\nimport com.example.geom.Circle\n",
        )
        .unwrap();
        let files = scan_kotlin_files(&tmp);
        assert_eq!(files.len(), 2);
        let deps = collect_kotlin_import_graph(&files, &tmp);
        let runner_deps = deps.get("src/Runner.kt").unwrap();
        assert_eq!(runner_deps, &vec!["src/com/example/geom/Shape.kt".to_string()]);
        std::fs::remove_dir_all(&tmp).ok();
    }
}

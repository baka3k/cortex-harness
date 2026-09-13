//! Port `_scan_java_files` + `_collect_java_import_graph` +
//! `_expand_impacted_files_by_imports` của java_analyzer.py.
//!
//! Lưu ý: java analyzer CÓ skip-list riêng (`_SCAN_SKIP_DIRS`, fnmatch-style)
//! union `COMMON_SCAN_EXCLUDE` — KHÔNG dùng `scan::scan_files` của framework
//! (list đó là python-specific).

use std::collections::{BTreeSet, HashMap, HashSet, VecDeque};
use std::path::{Path, PathBuf};

use cortex_analyzer_framework::scan::COMMON_SCAN_EXCLUDE;

/// `_SCAN_SKIP_DIRS` — set riêng của java_analyzer.py.
const JAVA_SCAN_SKIP_DIRS: [&str; 39] = [
    // Version control
    ".git",
    ".hg",
    ".svn",
    // IDE
    ".idea",
    ".vscode",
    ".settings",
    ".eclipse",
    "*.swp",
    "*.swo",
    // Build outputs
    "target",
    "build",
    "out",
    "bin",
    "buildSrc",
    // Gradle/Maven
    ".gradle",
    ".mvn",
    "mvnw",
    "mvnw.cmd",
    // Node (mixed projects)
    "node_modules",
    "dist",
    // Cache
    ".cache",
    ".parcel-cache",
    "__pycache__",
    // Testing
    "coverage",
    ".test-results",
    "junit",
    "test-results",
    // Temporary
    "tmp",
    "temp",
    ".tmp",
    "tmpdir",
    // OS
    ".DS_Store",
    "Thumbs.db",
    // Compiled
    "*.class",
    // Misc project files
    ".project",
    ".classpath",
    "*.iml",
    "*.ipr",
    "*.iws",
];

/// `fnmatch.fnmatch` POSIX (normcase = identity → case-sensitive).
fn fnmatch_matches(name: &str, pattern: &str) -> bool {
    let mut regex = String::from("(?s)^");
    let mut chars = pattern.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '*' => regex.push_str(".*"),
            '?' => regex.push('.'),
            '[' => {
                regex.push('[');
                if chars.peek() == Some(&'!') {
                    chars.next();
                    regex.push('^');
                }
                if chars.peek() == Some(&']') {
                    chars.next();
                    regex.push(']');
                }
                while let Some(&nc) = chars.peek() {
                    chars.next();
                    regex.push(nc);
                    if nc == ']' {
                        break;
                    }
                }
            }
            c => {
                if "\\.^$|(){}+".contains(c) {
                    regex.push('\\');
                }
                regex.push(c);
            }
        }
    }
    regex.push('$');
    regex::Regex::new(&regex)
        .map(|re| re.is_match(name))
        .unwrap_or(false)
}

/// `_is_skipped_name` — exact hoặc fnmatch trên skip-list + extra-ignore.
fn is_skipped_name(name: &str) -> bool {
    for pattern in JAVA_SCAN_SKIP_DIRS {
        if name == pattern || fnmatch_matches(name, pattern) {
            return true;
        }
    }
    for pattern in COMMON_SCAN_EXCLUDE {
        if name == pattern {
            return true;
        }
    }
    matches_extra_ignore(name)
}

/// `matches_extra_ignore` — CORTEX_EXTRA_IGNORE_DIRS (exact hoặc fnmatch).
fn matches_extra_ignore(name: &str) -> bool {
    let extra = std::env::var("CORTEX_EXTRA_IGNORE_DIRS").unwrap_or_default();
    if extra.is_empty() {
        return false;
    }
    extra
        .split(',')
        .map(str::trim)
        .filter(|entry| !entry.is_empty())
        .any(|pattern| name == pattern || fnmatch_matches(name, pattern))
}

/// `_scan_java_files` — walk root, skip names, thu .java, sort theo path str.
pub fn scan_java_files(root: &Path) -> Vec<PathBuf> {
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
                if !is_skipped_name(&name) {
                    subdirs.push(path);
                }
            }
            Ok(_) => {
                if !is_skipped_name(&name) && name.ends_with(".java") {
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
            // \r\n gộp 1 boundary.
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

/// `_extract_java_package_and_imports_from_text` — regex từng dòng.
pub fn extract_package_and_imports_from_text(text: &str) -> (Option<String>, Vec<String>) {
    let package_re = regex::Regex::new(r"^package\s+([A-Za-z_][A-Za-z0-9_\.]*)\s*;").expect("pkg re");
    let import_re =
        regex::Regex::new(r"^import\s+(?:static\s+)?([A-Za-z_][A-Za-z0-9_\.]*(?:\.\*)?)\s*;")
            .expect("import re");
    let mut package_name: Option<String> = None;
    let mut imports: Vec<String> = Vec::new();
    for raw_line in py_splitlines(text) {
        let line = raw_line.trim();
        if line.is_empty()
            || line.starts_with("//")
            || line.starts_with("/*")
            || line.starts_with('*')
        {
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

/// `_collect_java_import_graph` — rel_path → sorted deps (rel_path).
pub fn collect_java_import_graph(all_java_files: &[PathBuf], root: &Path) -> HashMap<String, Vec<String>> {
    let mut package_to_files: HashMap<String, BTreeSet<String>> = HashMap::new();
    let mut package_by_file: HashMap<String, Option<String>> = HashMap::new();
    let mut imports_by_file: HashMap<String, Vec<String>> = HashMap::new();
    let mut rel_paths: Vec<String> = Vec::new();

    for abs_path in all_java_files {
        let rel_path = cortex_analyzer_framework::scan::rel_posix(root, abs_path);
        rel_paths.push(rel_path.clone());
        let text = match std::fs::read_to_string(abs_path) {
            Ok(text) => text,
            Err(_) => {
                package_by_file.insert(rel_path.clone(), None);
                imports_by_file.insert(rel_path.clone(), Vec::new());
                continue;
            }
        };
        // Python mở với encoding="utf-8", errors="ignore".
        let text = cortex_analyzer_framework::ts::decode_ignore(text.as_bytes());
        let (package_name, imports) = extract_package_and_imports_from_text(&text);
        package_by_file.insert(rel_path.clone(), package_name.clone());
        imports_by_file.insert(rel_path.clone(), imports.clone());
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
        if let Some(Some(package_name)) = package_by_file.get(rel_path)
            && let Some(files) = package_to_files.get(package_name)
        {
            resolved.extend(files.iter().cloned());
        }
        for imp in imports_by_file.get(rel_path).into_iter().flatten() {
            if let Some(stripped) = imp.strip_suffix(".*") {
                if let Some(files) = package_to_files.get(stripped) {
                    resolved.extend(files.iter().cloned());
                }
            } else {
                if imp.contains('.')
                    && let Some(last_dot) = imp.rfind('.')
                    && let Some(files) = package_to_files.get(&imp[..last_dot])
                {
                    resolved.extend(files.iter().cloned());
                }
                if let Some(files) = package_to_files.get(imp) {
                    resolved.extend(files.iter().cloned());
                }
            }
        }
        resolved.remove(rel_path);
        deps_by_file.insert(rel_path.clone(), resolved.into_iter().collect());
    }
    deps_by_file
}

/// `_expand_impacted_files_by_imports` — BFS FIFO qua reverse deps.
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
    fn skip_names_match_java_list() {
        assert!(is_skipped_name("build"));
        assert!(is_skipped_name("Foo.class"));
        assert!(is_skipped_name("notes.iml"));
        assert!(!is_skipped_name("Circle.java"));
        assert!(!is_skipped_name("src"));
    }

    #[test]
    fn fnmatch_star() {
        assert!(fnmatch_matches("a.class", "*.class"));
        assert!(fnmatch_matches("a.swp", "*.swp"));
        assert!(!fnmatch_matches("a.java", "*.class"));
    }

    #[test]
    fn package_import_extraction() {
        let text = "// lead\npackage a.b;\n\nimport java.util.List;\nimport static x.Y.z;\nimport a.b.*;\n";
        let (pkg, imports) = extract_package_and_imports_from_text(text);
        assert_eq!(pkg.as_deref(), Some("a.b"));
        // Regex Python giữ suffix `.*` (xử lý endswith(".*") ở import graph).
        assert_eq!(imports, vec!["java.util.List", "x.Y.z", "a.b.*"]);
    }
}

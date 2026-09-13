//! Port `python_analyzer.py::_scan_python_files` + `_should_ignore_directory`
//! + `tools/common/scan_ignore.py` (COMMON_SCAN_EXCLUDE + extra-ignore env).

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

pub const EXTRA_IGNORE_DIRS_ENV_VAR: &str = "CORTEX_EXTRA_IGNORE_DIRS";

/// `COMMON_SCAN_EXCLUDE` — mirrors `cortex_harness/dev.py::_SCAN_EXCLUDE`.
pub const COMMON_SCAN_EXCLUDE: [&str; 89] = [
    // Version control
    ".git",
    ".hg",
    ".svn",
    // Python
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".pytype",
    ".pyre",
    ".hypothesis",
    ".ipynb_checkpoints",
    ".eggs",
    "htmlcov",
    // JavaScript / TypeScript / frontend frameworks
    "node_modules",
    "bower_components",
    ".next",
    ".nuxt",
    ".output",
    ".svelte-kit",
    ".astro",
    ".parcel-cache",
    ".turbo",
    ".nx",
    ".vite",
    ".pnpm-store",
    ".npm",
    // Java / Kotlin / Scala / Android
    ".gradle",
    ".kotlin",
    ".bloop",
    ".metals",
    ".bsp",
    ".scala-build",
    // .NET
    ".vs",
    "TestResults",
    "BenchmarkDotNet.Artifacts",
    // C / C++ / CMake / Meson / Bazel / Conan
    "CMakeFiles",
    "_deps",
    ".ccache",
    ".conan",
    "meson-private",
    "meson-logs",
    "meson-info",
    // Apple / Swift / Objective-C
    "Pods",
    "DerivedData",
    "xcuserdata",
    // Dart / Flutter
    ".dart_tool",
    ".pub-cache",
    ".flutter-plugins",
    ".flutter-plugins-dependencies",
    // Ruby
    ".bundle",
    ".yardoc",
    "_yardoc",
    // Elixir / Erlang / OCaml
    "_build",
    ".elixir_ls",
    ".lexical",
    ".opam-switch",
    // Haskell
    ".stack-work",
    "dist-newstyle",
    // Infrastructure / cloud tooling
    ".terraform",
    ".terragrunt-cache",
    ".serverless",
    ".aws-sam",
    "cdk.out",
    // Generic build output
    "build",
    "out",
    "target",
    "dist",
    "bin",
    "obj",
    "artifacts",
    // Dependencies
    "vendor",
    // Test / coverage output
    "coverage",
    ".nyc_output",
    "lcov-report",
    "playwright-report",
    "test-results",
    "allure-results",
    "allure-report",
    // IDE and local tooling
    ".idea",
    ".vscode",
    ".fleet",
    ".cache",
    ".scannerwork",
    ".cortext-harness",
];

/// `_should_ignore_directory` — ignore set của python_analyzer (bao gồm
/// COMMON_SCAN_EXCLUDE qua `_common_scan_exclude`).
pub fn should_ignore_directory(dir_name: &str) -> bool {
    let analyzer_ignores = [
        "venv",
        ".venv",
        "env",
        "virtualenv",
        "env.bak",
        "venv.bak",
        ".env.bak",
        ".venv.bak",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".cache",
        "dist",
        "build",
        ".eggs",
        ".tox",
        ".coverage",
        "htmlcov",
        "pytest_cache",
        ".idea",
        ".vscode",
        ".git",
        ".svn",
        ".hg",
        "temp",
        "tmp",
        ".tmp",
        "tmpdir",
        "node_modules",
        ".DS_Store",
        "Thumbs.db",
    ];
    if analyzer_ignores.contains(&dir_name) || COMMON_SCAN_EXCLUDE.contains(&dir_name) {
        return true;
    }
    if dir_name.ends_with(".egg-info") || dir_name.ends_with(".swp") || dir_name.ends_with(".swo")
    {
        return true;
    }
    matches_extra_ignore(dir_name)
}

/// `matches_extra_ignore` — user-configured set từ env (exact hoặc fnmatch).
pub fn matches_extra_ignore(name: &str) -> bool {
    let extra = extra_ignore_dirs();
    if extra.is_empty() {
        return false;
    }
    extra
        .iter()
        .any(|pattern| pattern == name || matches_pattern(name, pattern))
}

fn extra_ignore_dirs() -> Vec<String> {
    std::env::var(EXTRA_IGNORE_DIRS_ENV_VAR)
        .unwrap_or_default()
        .split(',')
        .map(|entry| entry.trim().to_string())
        .filter(|entry| !entry.is_empty())
        .collect()
}

/// `fnmatch`-style match — Python `fnmatch.fnmatch` áp `os.path.normcase`:
/// case-insensitive trên darwin/windows, giữ nguyên trên linux.
fn matches_pattern(name: &str, pattern: &str) -> bool {
    if !pattern.contains('*') && !pattern.contains('?') && !pattern.contains('[') {
        return false;
    }
    fnmatch_matches(pattern, name)
}

/// fnmatch translate: `*` → `.*`, `?` → `.`, `[...]` giữ nguyên charclass.
fn fnmatch_matches(pattern: &str, name: &str) -> bool {
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
    // normcase(darwin) = lowercase cả pattern lẫn name.
    let regex = format!("(?i){regex}");
    regex::Regex::new(&regex).map(|re| re.is_match(name)).unwrap_or(false)
}

/// `_scan_python_files` — walk root, bỏ ignored dirs, thu `.py`/`.pyi`,
/// trả sorted list. `extra_extensions` cho analyzer khác ngôn ngữ.
pub fn scan_files(root: &Path, extensions: &[&str]) -> Vec<PathBuf> {
    let mut files = BTreeSet::new();
    walk(root, root, extensions, &mut files);
    files.into_iter().collect()
}

fn walk(root: &Path, dir: &Path, extensions: &[&str], files: &mut BTreeSet<PathBuf>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut subdirs: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        let file_type = match entry.file_type() {
            Ok(t) => t,
            Err(_) => continue,
        };
        if file_type.is_dir() {
            if !should_ignore_directory(&name) {
                subdirs.push(path);
            }
            continue;
        }
        if name.ends_with(".swp")
            || name.ends_with(".swo")
            || name.ends_with(".pyc")
            || name.ends_with(".pyo")
            || name == ".DS_Store"
            || name == "Thumbs.db"
            || name.starts_with(".env")
        {
            continue;
        }
        if extensions.iter().any(|ext| name.ends_with(ext)) {
            files.insert(path);
        }
    }
    // `root` giữ cho tính rel-path; đệ quy BFS như os.walk (topdown).
    let _ = root;
    for sub in subdirs {
        walk(root, &sub, extensions, files);
    }
}

/// rel-path posix từ root (khớp `os.path.relpath(...).replace(os.sep, "/")`).
pub fn rel_posix(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .components()
        .map(|c| c.as_os_str().to_string_lossy().to_string())
        .collect::<Vec<_>>()
        .join("/")
}

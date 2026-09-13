//! Port of the input-scan + text-reader helpers of
//! `doc-tiny/graphrag_ingest_langextract.py` (`read_text_file`,
//! `_iter_input_files`, `_safe_source_id`) plus the `code-tiny`
//! `tools.common.scan_ignore` exclude set that the Python module imports.

use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};

/// `_DOC_INPUT_EXTENSIONS`.
pub const DOC_INPUT_EXTENSIONS: [&str; 6] = [".pdf", ".txt", ".md", ".docx", ".pptx", ".xlsx"];

/// `COMMON_SCAN_EXCLUDE` from `code-tiny/tools/common/scan_ignore.py`
/// (89 entries — mirrors the orchestrator's `_SCAN_EXCLUDE`).
pub const COMMON_SCAN_EXCLUDE: [&str; 89] = [
    ".astro",
    ".aws-sam",
    ".bloop",
    ".bsp",
    ".bundle",
    ".cache",
    ".ccache",
    ".conan",
    ".cortext-harness",
    ".dart_tool",
    ".eggs",
    ".elixir_ls",
    ".fleet",
    ".flutter-plugins",
    ".flutter-plugins-dependencies",
    ".git",
    ".gradle",
    ".hg",
    ".hypothesis",
    ".idea",
    ".ipynb_checkpoints",
    ".kotlin",
    ".lexical",
    ".metals",
    ".mypy_cache",
    ".next",
    ".nox",
    ".npm",
    ".nuxt",
    ".nx",
    ".nyc_output",
    ".opam-switch",
    ".output",
    ".parcel-cache",
    ".pnpm-store",
    ".pub-cache",
    ".pyre",
    ".pytest_cache",
    ".pytype",
    ".ruff_cache",
    ".scala-build",
    ".scannerwork",
    ".serverless",
    ".stack-work",
    ".svelte-kit",
    ".svn",
    ".terraform",
    ".terragrunt-cache",
    ".tox",
    ".turbo",
    ".venv",
    ".vite",
    ".vs",
    ".vscode",
    ".yardoc",
    "BenchmarkDotNet.Artifacts",
    "CMakeFiles",
    "DerivedData",
    "Pods",
    "TestResults",
    "__pycache__",
    "_build",
    "_deps",
    "_yardoc",
    "allure-report",
    "allure-results",
    "artifacts",
    "bin",
    "bower_components",
    "build",
    "cdk.out",
    "coverage",
    "dist",
    "dist-newstyle",
    "env",
    "htmlcov",
    "lcov-report",
    "meson-info",
    "meson-logs",
    "meson-private",
    "node_modules",
    "obj",
    "out",
    "playwright-report",
    "target",
    "test-results",
    "vendor",
    "venv",
    "xcuserdata",
];

/// Port of `extra_ignore_dirs()` — `CORTEX_EXTRA_IGNORE_DIRS`, comma separated.
pub fn extra_ignore_dirs() -> Vec<String> {
    match std::env::var("CORTEX_EXTRA_IGNORE_DIRS") {
        Ok(raw) => raw
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .collect(),
        Err(_) => Vec::new(),
    }
}

fn glob_to_regex(pattern: &str) -> Option<regex::Regex> {
    if !pattern.contains('*') && !pattern.contains('?') && !pattern.contains('[') {
        return None;
    }
    let mut out = String::from("^");
    let mut chars = pattern.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '*' => out.push_str(".*"),
            '?' => out.push('.'),
            '[' => {
                let mut class = String::from("[");
                if chars.peek() == Some(&'!') {
                    chars.next();
                    class.push('^');
                }
                for inner in chars.by_ref() {
                    if inner == ']' {
                        break;
                    }
                    let inner_str = inner.to_string();
                    if regex::escape(&inner_str) != inner_str {
                        class.push_str(&regex::escape(&inner_str));
                    } else {
                        class.push(inner);
                    }
                }
                class.push(']');
                out.push_str(&class);
            }
            other => out.push_str(&regex::escape(&other.to_string())),
        }
    }
    out.push('$');
    regex::Regex::new(&out).ok()
}

/// Port of `matches_extra_ignore(name)` — exact match or fnmatch glob against
/// the user-configured set.
pub fn matches_extra_ignore(name: &str) -> bool {
    matches_extra_ignore_from(name, &extra_ignore_dirs())
}

/// Pure core of [`matches_extra_ignore`] over an already-parsed entry list.
pub fn matches_extra_ignore_from(name: &str, extra: &[String]) -> bool {
    if extra.is_empty() {
        return false;
    }
    if extra.iter().any(|e| e == name) {
        return true;
    }
    extra
        .iter()
        .filter_map(|pattern| glob_to_regex(pattern))
        .any(|re| re.is_match(name))
}

/// Port of `read_text_file` (`path.read_text(encoding="utf-8").strip()`).
pub fn read_text_file(path: &Path) -> std::io::Result<String> {
    let raw = fs::read_to_string(path)?;
    Ok(raw
        .trim_matches(|c: char| c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}'))
        .to_string())
}

/// Port of `_safe_source_id(base, file_path)` — relative posix path with
/// separators replaced by `__`.
pub fn safe_source_id(base: &Path, file_path: &Path) -> std::io::Result<String> {
    let rel = file_path
        .strip_prefix(base)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidInput, e))?;
    Ok(rel
        .components()
        .map(|c| c.as_os_str().to_string_lossy())
        .collect::<Vec<_>>()
        .join("__"))
}

fn dir_part_excluded(part: &str) -> bool {
    COMMON_SCAN_EXCLUDE.contains(&part) || matches_extra_ignore(part)
}

/// Port of `_iter_input_files(folder)` — recursive scan for supported
/// document files, pruning excluded directory components, sorted by path.
///
/// Python sorts `Path` objects by their full path **string**, so the Rust
/// side sorts by the raw path string as well (component-wise `PathBuf` Ord
/// would order `a/b.md` before `a.md`, unlike Python).
pub fn iter_input_files(folder: &Path) -> std::io::Result<Vec<PathBuf>> {
    let mut files: Vec<(String, PathBuf)> = Vec::new();
    collect_files(folder, folder, &mut files)?;
    files.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(files.into_iter().map(|(_, p)| p).collect())
}

fn collect_files(
    root: &Path,
    dir: &Path,
    files: &mut Vec<(String, PathBuf)>,
) -> std::io::Result<()> {
    let entries = match fs::read_dir(dir) {
        Ok(entries) => entries,
        // Python `rglob` silently skips unreadable directories.
        Err(_) => return Ok(()),
    };
    for entry in entries {
        let entry = match entry {
            Ok(e) => e,
            Err(_) => continue,
        };
        let path = entry.path();
        let Ok(meta) = fs::metadata(&path) else {
            continue;
        };
        if meta.is_dir() {
            collect_files(root, &path, files)?;
            continue;
        }
        if !meta.is_file() {
            continue;
        }
        let name = path
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        if name.starts_with("~$") {
            continue;
        }
        let suffix = path
            .extension()
            .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
            .unwrap_or_default();
        if !DOC_INPUT_EXTENSIONS.contains(&suffix.as_str()) {
            continue;
        }
        let relative = match path.strip_prefix(root) {
            Ok(rel) => rel,
            Err(_) => continue,
        };
        let parents: Vec<&std::ffi::OsStr> = relative
            .components()
            .map(|c| c.as_os_str())
            .collect::<Vec<_>>();
        let parents = &parents[..parents.len().saturating_sub(1)];
        if parents
            .iter()
            .any(|p| dir_part_excluded(&p.to_string_lossy()))
        {
            continue;
        }
        files.push((path.to_string_lossy().into_owned(), path));
    }
    Ok(())
}

/// Simple set helper used by tests / diagnostics.
pub fn exclude_set() -> HashSet<&'static str> {
    COMMON_SCAN_EXCLUDE.into()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exclude_set_size_matches_python() {
        assert_eq!(COMMON_SCAN_EXCLUDE.len(), 89);
        assert_eq!(exclude_set().len(), 89);
    }

    #[test]
    fn extra_ignore_exact_and_glob() {
        let extra: Vec<String> = vec!["secret-props".into(), "*.egg-info".into()];
        assert!(matches_extra_ignore_from("secret-props", &extra));
        assert!(matches_extra_ignore_from("cortex_harness.egg-info", &extra));
        assert!(!matches_extra_ignore_from("docs", &extra));
        assert!(!matches_extra_ignore_from("docs", &[]));
    }

    #[test]
    fn safe_source_id_joins_with_double_underscore() {
        let base = Path::new("/tmp/corpus");
        let file = Path::new("/tmp/corpus/guides/ops-runbook.md");
        assert_eq!(
            safe_source_id(base, file).unwrap(),
            "guides__ops-runbook.md"
        );
    }
}

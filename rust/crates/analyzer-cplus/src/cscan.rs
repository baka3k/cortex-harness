//! Port phần scan của `cplus_analyzer.py`: `_SCAN_SKIP_DIRS` + COMMON_SCAN_EXCLUDE,
//! `_scan_c_family_files`, `_is_cpp_file`, `_looks_like_cpp_header`,
//! `legacy_encoding.decode_legacy_bytes`, `rc_parser.decode_rc_bytes`,
//! `_collect_include_graph`, `_expand_impacted_files_by_includes`.

use std::collections::{BTreeSet, HashMap, HashSet, VecDeque};
use std::path::{Path, PathBuf};

use crate::position::{basename, dirname, normpath, splitext};

/// `COMMON_SCAN_EXCLUDE` (tools/common/scan_ignore.py).
pub const COMMON_SCAN_EXCLUDE: [&str; 89] = [
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__", ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".pytype", ".pyre", ".hypothesis",
    ".ipynb_checkpoints", ".eggs", "htmlcov", "node_modules", "bower_components",
    ".next", ".nuxt", ".output", ".svelte-kit", ".astro", ".parcel-cache", ".turbo",
    ".nx", ".vite", ".pnpm-store", ".npm", ".gradle", ".kotlin", ".bloop", ".metals",
    ".bsp", ".scala-build", ".vs", "TestResults", "BenchmarkDotNet.Artifacts",
    "CMakeFiles", "_deps", ".ccache", ".conan", "meson-private", "meson-logs",
    "meson-info", "Pods", "DerivedData", "xcuserdata", ".dart_tool", ".pub-cache",
    ".flutter-plugins", ".flutter-plugins-dependencies", ".bundle", ".yardoc",
    "_yardoc", "_build", ".elixir_ls", ".lexical", ".opam-switch", ".stack-work",
    "dist-newstyle", ".terraform", ".terragrunt-cache", ".serverless", ".aws-sam",
    "cdk.out", "build", "out", "target", "dist", "bin", "obj", "artifacts", "vendor",
    "coverage", ".nyc_output", "lcov-report", "playwright-report", "test-results",
    "allure-results", "allure-report", ".idea", ".vscode", ".fleet", ".cache",
    ".scannerwork", ".cortext-harness",
];

/// `_SCAN_SKIP_DIRS` riêng của cplus_analyzer.py (union COMMON_SCAN_EXCLUDE).
///
/// LƯU Ý parity: Python kiểm `name not in _SCAN_SKIP_DIRS` — membership CHÍNH
/// XÁC theo chuỗi, các entry glob (`*.o`, `moc_*`, ...) chỉ match thư mục trùng
/// tên literal, không qua fnmatch. Port giữ nguyên semantics này.
pub const SCAN_SKIP_DIRS: [&str; 45] = [
    ".git", ".hg", ".svn", ".idea", ".vs", ".vscode", ".eclipse", ".settings",
    "build", "out", "bin", "obj", "cmake-build-*", "CMakeFiles", "CMakeCache.txt",
    "cmake_install.cmake", "Makefile", "*.o", "*.obj", "*.so", "*.dll", "*.dylib",
    "*.a", "*.lib", "*.exe", "*.gch", "*.pch", ".gradle", ".externalNativeBuild",
    "node_modules", "dist", "target", "moc_*", "ui_*", "qrc_*.cpp", ".cache",
    ".parcel-cache", "__pycache__", "coverage", ".test-results", "test-results",
    "tmp", "temp", ".tmp", "tmpdir",
];

/// `matches_extra_ignore` — env `CORTEX_EXTRA_IGNORE_DIRS` (exact hoặc fnmatch).
pub fn matches_extra_ignore(name: &str) -> bool {
    let extra: Vec<String> = std::env::var("CORTEX_EXTRA_IGNORE_DIRS")
        .unwrap_or_default()
        .split(',')
        .map(|entry| entry.trim().to_string())
        .filter(|entry| !entry.is_empty())
        .collect();
    if extra.is_empty() {
        return false;
    }
    extra
        .iter()
        .any(|pattern| pattern == name || fnmatch_matches(pattern, name))
}

fn fnmatch_matches(pattern: &str, name: &str) -> bool {
    if !pattern.contains('*') && !pattern.contains('?') && !pattern.contains('[') {
        return false;
    }
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
    // macOS fnmatch normcase → lowercase (như framework scan.rs).
    let regex = format!("(?i){regex}");
    regex::Regex::new(&regex)
        .map(|re| re.is_match(name))
        .unwrap_or(false)
}

fn skip_dir(name: &str) -> bool {
    SCAN_SKIP_DIRS.contains(&name) || COMMON_SCAN_EXCLUDE.contains(&name) || matches_extra_ignore(name)
}

const CPLUS_EXTENSIONS: [&str; 12] = [
    ".c", ".h", ".hpp", ".cpp", ".cc", ".cxx", ".hh", ".hxx", ".pc", ".pcc", ".rc",
    ".rc2",
];

/// `_scan_c_family_files` — walk + sort theo chuỗi path tuyệt đối (Python
/// `sorted(files)` so chuỗi).
pub fn scan_c_family_files(root: &Path) -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = Vec::new();
    walk(root, &mut files);
    files.sort_by(|a, b| a.to_string_lossy().cmp(&b.to_string_lossy()));
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
        let Ok(file_type) = entry.file_type() else {
            continue;
        };
        if file_type.is_dir() {
            if !skip_dir(&name) {
                subdirs.push(path);
            }
            continue;
        }
        if !file_type.is_file() {
            continue;
        }
        let lower = name.to_lowercase();
        if CPLUS_EXTENSIONS.iter().any(|ext| lower.ends_with(ext)) {
            files.push(path);
        }
    }
    subdirs.sort();
    for sub in subdirs {
        walk(&sub, files);
    }
}

/// `_looks_like_cpp_header`.
pub fn looks_like_cpp_header(path: &Path) -> bool {
    let Ok(chunk) = std::fs::read(path) else {
        return false;
    };
    let chunk = &chunk[..chunk.len().min(65536)];
    let text = String::from_utf8_lossy(chunk);
    lazy_static_cpp_hint().is_match(&text)
}

fn lazy_static_cpp_hint() -> &'static regex::Regex {
    use std::sync::OnceLock;
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    RE.get_or_init(|| {
        regex::Regex::new(r"\b(namespace|class|template|typename|operator|using\s+namespace|constexpr)\b|::")
            .expect("cpp hint regex")
    })
}

/// `_is_cpp_file` — heuristic khi không có compile db.
pub fn is_cpp_file(path: &Path, _root: &Path) -> bool {
    let lower = path.to_string_lossy().to_lowercase();
    let ext = splitext(&lower).1;
    if [".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"].contains(&ext.as_str()) {
        return true;
    }
    if ext == ".c" {
        return false;
    }
    if ext != ".h" {
        return false;
    }
    let abs_path = path.to_string_lossy().to_string();
    let stem = splitext(&abs_path).0;
    for suffix in [".cpp", ".cc", ".cxx"] {
        if Path::new(&format!("{stem}{suffix}")).exists() {
            return true;
        }
    }
    if Path::new(&format!("{stem}.c")).exists() {
        return false;
    }
    looks_like_cpp_header(path)
}

/// Kết quả decode: text + tên encoding + lossy.
#[derive(Debug, Clone)]
pub struct DecodedText {
    pub text: String,
    pub encoding: String,
    pub lossy: bool,
}

fn utf16_strict_decode(data: &[u8], big_endian: bool) -> Option<String> {
    let mut units: Vec<u16> = Vec::with_capacity(data.len() / 2);
    let mut chunks = data.chunks_exact(2);
    for chunk in &mut chunks {
        let unit = if big_endian {
            u16::from_be_bytes([chunk[0], chunk[1]])
        } else {
            u16::from_le_bytes([chunk[0], chunk[1]])
        };
        units.push(unit);
    }
    String::from_utf16(&units).ok()
}

/// `legacy_encoding.decode_legacy_bytes` — UTF-16 BOM/NUL-heuristic, BOM
/// UTF-8, sau đó utf-8 strict → cp932 strict → cp1252 replace (lossy).
pub fn decode_legacy_bytes(data: &[u8]) -> DecodedText {
    if data.starts_with(&[0xff, 0xfe])
        && let Some(text) = utf16_strict_decode(&data[2..], false) {
            return DecodedText { text, encoding: "utf-16-le".into(), lossy: false };
        }
    if data.starts_with(&[0xfe, 0xff])
        && let Some(text) = utf16_strict_decode(&data[2..], true) {
            return DecodedText { text, encoding: "utf-16-be".into(), lossy: false };
        }
    if data.starts_with(&[0xef, 0xbb, 0xbf])
        && let Ok(text) = std::str::from_utf8(&data[3..]) {
            return DecodedText { text: text.to_string(), encoding: "utf-8-sig".into(), lossy: false };
        }
    let sample = &data[..data.len().min(512)];
    if !sample.is_empty() {
        let odd_nuls = sample.iter().skip(1).step_by(2).filter(|&&b| b == 0).count();
        let even_nuls = sample.iter().step_by(2).filter(|&&b| b == 0).count();
        let threshold = (sample.len() / 8).max(2);
        if odd_nuls >= threshold
            && let Some(text) = utf16_strict_decode(data, false) {
                return DecodedText { text, encoding: "utf-16-le".into(), lossy: false };
            }
        if even_nuls >= threshold
            && let Some(text) = utf16_strict_decode(data, true) {
                return DecodedText { text, encoding: "utf-16-be".into(), lossy: false };
            }
    }
    if let Ok(text) = std::str::from_utf8(data) {
        return DecodedText { text: text.to_string(), encoding: "utf-8".into(), lossy: false };
    }
    // Python: data.decode("cp932") strict — cp932 ≈ Windows-31J.
    let (text, _, had_errors) = encoding_rs::SHIFT_JIS.decode(data);
    if !had_errors {
        return DecodedText { text: text.into_owned(), encoding: "cp932".into(), lossy: false };
    }
    let (text, _, _) = encoding_rs::WINDOWS_1252.decode(data);
    DecodedText { text: text.into_owned(), encoding: "cp1252".into(), lossy: true }
}

/// `read_legacy_text`.
pub fn read_legacy_text(path: &Path) -> DecodedText {
    match std::fs::read(path) {
        Ok(data) => decode_legacy_bytes(&data),
        Err(_) => DecodedText { text: String::new(), encoding: "utf-8".into(), lossy: false },
    }
}

/// `rc_parser.decode_rc_bytes` — như decode_legacy_bytes nhưng thứ tự thử
/// utf-8 → cp932 (strict) rồi cp1252 replace; KHÔNG có nhánh utf-8-sig
/// decode-fail (decode("utf-8-sig") strict).
pub fn decode_rc_bytes(data: &[u8]) -> DecodedText {
    if data.starts_with(&[0xff, 0xfe])
        && let Some(text) = utf16_strict_decode(&data[2..], false) {
            return DecodedText { text, encoding: "utf-16-le".into(), lossy: false };
        }
    if data.starts_with(&[0xfe, 0xff])
        && let Some(text) = utf16_strict_decode(&data[2..], true) {
            return DecodedText { text, encoding: "utf-16-be".into(), lossy: false };
        }
    if data.starts_with(&[0xef, 0xbb, 0xbf])
        && let Ok(text) = std::str::from_utf8(&data[3..]) {
            return DecodedText { text: text.to_string(), encoding: "utf-8-sig".into(), lossy: false };
        }
    let sample = &data[..data.len().min(512)];
    if !sample.is_empty() {
        let odd_nuls = sample.iter().skip(1).step_by(2).filter(|&&b| b == 0).count();
        let even_nuls = sample.iter().step_by(2).filter(|&&b| b == 0).count();
        let threshold = (sample.len() / 8).max(2);
        if odd_nuls >= threshold
            && let Some(text) = utf16_strict_decode(data, false) {
                return DecodedText { text, encoding: "utf-16-le".into(), lossy: false };
            }
        if even_nuls >= threshold
            && let Some(text) = utf16_strict_decode(data, true) {
                return DecodedText { text, encoding: "utf-16-be".into(), lossy: false };
            }
    }
    for (encoding, name) in [
        (encoding_rs::UTF_8, "utf-8"),
        (encoding_rs::SHIFT_JIS, "cp932"),
    ] {
        let (text, _, had_errors) = encoding.decode(data);
        if !had_errors {
            return DecodedText { text: text.into_owned(), encoding: name.into(), lossy: false };
        }
    }
    let (text, _, _) = encoding_rs::WINDOWS_1252.decode(data);
    DecodedText { text: text.into_owned(), encoding: "cp1252".into(), lossy: true }
}

/// `_extract_includes` — line-based `#include` regex.
pub fn extract_includes(text: &str) -> Vec<String> {
    let mut includes = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if !line.starts_with("#include") {
            continue;
        }
        if let Some(caps) = include_name_regex().captures(line) {
            includes.push(caps[1].to_string());
        }
    }
    includes
}

fn include_name_regex() -> &'static regex::Regex {
    use std::sync::OnceLock;
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r#"#include\s+[<"]([^>"]+)[>"]"#).expect("include regex"))
}

/// `_extract_macros` — `#define NAME value` (có hoặc không param list).
pub fn extract_macros(text: &str) -> HashMap<String, String> {
    let mut macros = HashMap::new();
    for line in text.lines() {
        let line = line.trim();
        if !line.starts_with("#define") {
            continue;
        }
        if let Some(caps) = macro_regex().captures(line) {
            let name = &caps[1];
            let expansion = caps[3].trim().to_string();
            if !name.is_empty() && !expansion.is_empty() {
                macros.insert(name.to_string(), expansion);
            }
        }
    }
    macros
}

fn macro_regex() -> &'static regex::Regex {
    use std::sync::OnceLock;
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    RE.get_or_init(|| {
        regex::Regex::new(r"#define\s+([A-Za-z_][A-Za-z0-9_]*)(\s*\([^)]*\))?\s+(.*)")
            .expect("macro regex")
    })
}

fn is_windows_resource_file(path: &Path) -> bool {
    matches!(
        splitext(&path.to_string_lossy().to_lowercase()).1.as_str(),
        ".rc" | ".rc2"
    )
}

/// `_collect_include_graph` — resolved include edges theo rel path.
pub fn collect_include_graph(all_file_paths: &[PathBuf], root: &Path) -> HashMap<String, Vec<String>> {
    let mut rel_to_abs: HashMap<String, PathBuf> = HashMap::new();
    for path in all_file_paths {
        let rel = rel_posix(root, path);
        rel_to_abs.insert(rel, path.clone());
    }
    let mut basename_to_rel: HashMap<String, Vec<String>> = HashMap::new();
    for rel in rel_to_abs.keys() {
        basename_to_rel
            .entry(basename(rel))
            .or_default()
            .push(rel.clone());
    }

    let resolve_include = |source_rel: &str, include_name: &str| -> Option<String> {
        let include_norm = include_name.replace('\\', "/");
        if include_norm.contains('/') {
            let candidate =
                normpath(&format!("{}/{include_norm}", dirname(source_rel)));
            if rel_to_abs.contains_key(&candidate) {
                return Some(candidate);
            }
            let candidate = normpath(&include_norm);
            if rel_to_abs.contains_key(&candidate) {
                return Some(candidate);
            }
        }
        let matches = basename_to_rel.get(&basename(&include_norm))?;
        if matches.len() == 1 {
            return Some(matches[0].clone());
        }
        let source_dir_owned = dirname(source_rel);
        let source_parts: Vec<&str> = source_dir_owned
            .split('/')
            .filter(|p| !p.is_empty())
            .collect();
        let score = |rel_path: &str| -> (i64, i64, String) {
            let cand_dir_owned = dirname(rel_path);
            let cand_parts: Vec<&str> = cand_dir_owned
                .split('/')
                .filter(|p| !p.is_empty())
                .collect();
            let mut common = 0i64;
            for (left, right) in source_parts.iter().rev().zip(cand_parts.iter().rev()) {
                if left != right {
                    break;
                }
                common += 1;
            }
            let distance =
                (source_parts.len() as i64 - cand_parts.len() as i64).abs();
            (common, -distance, rel_path.to_string())
        };
        matches
            .iter()
            .max_by(|a, b| score(a).cmp(&score(b)))
            .cloned()
    };

    let mut deps_by_file: HashMap<String, Vec<String>> = HashMap::new();
    for (rel_path, abs_path) in &rel_to_abs {
        let source_text = if is_windows_resource_file(abs_path) {
            crate::rcparse::read_rc_text(abs_path).0
        } else {
            // Python open(..., errors="ignore") — drop invalid bytes.
            match std::fs::read(abs_path) {
                Ok(bytes) => crate::cparse::decode_ignore_bytes(&bytes),
                Err(_) => String::new(),
            }
        };
        let includes = extract_includes(&source_text);
        let mut resolved: Vec<String> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        for include_name in includes {
            let Some(target_rel) = resolve_include(rel_path, &include_name) else {
                continue;
            };
            if target_rel == *rel_path || !seen.insert(target_rel.clone()) {
                continue;
            }
            resolved.push(target_rel);
        }
        deps_by_file.insert(rel_path.clone(), resolved);
    }
    deps_by_file
}

/// rel-path posix từ root.
pub fn rel_posix(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .components()
        .map(|c| c.as_os_str().to_string_lossy().to_string())
        .collect::<Vec<_>>()
        .join("/")
}

/// `_expand_impacted_files_by_includes` — BFS trên reverse include deps,
/// duyệt dependent theo thứ tự sorted.
pub fn expand_impacted_files_by_includes(
    changed_existing: &HashSet<String>,
    deps_by_file: &HashMap<String, Vec<String>>,
) -> BTreeSet<String> {
    let mut reverse_map: HashMap<String, BTreeSet<String>> = HashMap::new();
    for (source, deps) in deps_by_file {
        for dep in deps {
            reverse_map.entry(dep.clone()).or_default().insert(source.clone());
        }
    }
    let mut impacted: BTreeSet<String> = BTreeSet::new();
    let mut queue: VecDeque<String> = changed_existing.iter().cloned().collect();
    let mut seen: HashSet<String> = changed_existing.clone();
    while let Some(current) = queue.pop_front() {
        if let Some(dependents) = reverse_map.get(&current) {
            for dependent in dependents {
                if !seen.insert(dependent.clone()) {
                    continue;
                }
                impacted.insert(dependent.clone());
                queue.push_back(dependent.clone());
            }
        }
    }
    impacted
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::position::relpath;

    #[test]
    fn normpath_python_semantics() {
        assert_eq!(normpath("a/b/../c"), "a/c");
        assert_eq!(normpath("../x"), "../x");
        assert_eq!(normpath("/a/../../b"), "/b");
        assert_eq!(normpath("a//b/./c/"), "a/b/c");
    }

    #[test]
    fn relpath_python_semantics() {
        assert_eq!(relpath("/a/b/c.txt", "/a"), "b/c.txt");
        assert_eq!(relpath("/a/x/y.cpp", "/a/b"), "../x/y.cpp");
        assert_eq!(relpath("/a/b", "/a/b"), ".");
    }

    #[test]
    fn decode_utf8_and_utf16() {
        let d = decode_legacy_bytes("int x;".as_bytes());
        assert_eq!(d.encoding, "utf-8");
        let mut u16le: Vec<u8> = vec![0xff, 0xfe];
        for unit in "int x;".encode_utf16() {
            u16le.extend_from_slice(&unit.to_le_bytes());
        }
        let d = decode_legacy_bytes(&u16le);
        assert_eq!(d.encoding, "utf-16-le");
        assert_eq!(d.text, "int x;");
    }
}

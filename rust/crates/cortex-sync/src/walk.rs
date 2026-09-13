//! Source file discovery: `_SOURCE_EXTENSIONS`, `_SKIP_DIRS`, descriptor
//! spec matching (`descriptor_spec_for_path`), JP1 sniffing, extra-ignore
//! handling, and `_walk_all_source_files`.

use std::collections::BTreeSet;
use std::path::Path;

use crate::util;

pub const SOURCE_EXTENSIONS: [&str; 71] = [
    ".cbl", ".cob", ".cpy", ".copy",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".pc", ".pcc", ".rc", ".rc2",
    ".pas", ".dpr", ".inc",
    ".py",
    ".go",
    ".pl", ".pm", ".t",
    ".sh",
    ".rs", ".swift", ".dart", ".arb",
    ".js", ".jsx",
    ".ts", ".tsx",
    ".php",
    ".cs",
    ".sql",
    ".pls", ".plsql", ".pks", ".pkb", ".pkg", ".pck", ".spc", ".spb", ".trg", ".fnc",
    ".vb", ".vbproj", ".vbp", ".vbw", ".frx", ".bas", ".cls", ".frm", ".vbs", ".wsf", ".asp",
    ".java", ".kt", ".kts",
    ".xml", ".gradle",
    ".properties", ".yml", ".yaml", ".json",
    ".jsp", ".jspx", ".jspf", ".tag", ".tagx",
];

pub const SOURCE_EXTENSIONS_TAIL: [&str; 0] = [];

pub const SOURCE_EXTENSIONS_TAIL2: [&str; 0] = [];

/// `_SKIP_DIRS` from incremental_sync.py.
pub const SKIP_DIRS: [&str; 20] = [
    "node_modules", ".git", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", ".venv",
    "venv", ".idea", ".vscode", "dist", "build", ".next", ".nuxt", "target", "bin", "obj",
    ".cache", ".gradle", ".dart_tool",
];

pub fn is_skip_dir(name: &str) -> bool {
    SKIP_DIRS.contains(&name)
}

/// `matches_extra_ignore` — user-configured `CORTEX_EXTRA_IGNORE_DIRS`
/// (exact or fnmatch glob), comma-separated.
pub fn matches_extra_ignore(name: &str) -> bool {
    let raw = std::env::var("CORTEX_EXTRA_IGNORE_DIRS").unwrap_or_default();
    if raw.trim().is_empty() {
        return false;
    }
    for entry in raw.split(',') {
        let entry = entry.trim();
        if entry.is_empty() {
            continue;
        }
        if entry == name {
            return true;
        }
        if (entry.contains('*') || entry.contains('?') || entry.contains('['))
            && util::fnmatch(name, entry) {
                return true;
            }
    }
    false
}

/// Descriptor specs — name + glob patterns copied from
/// `tools/project_topology/registry.py::DESCRIPTOR_SPECS` (matching only;
/// the parsers themselves remain Python-plane).
const DESCRIPTOR_SPECS: [&str; 18] = [
    "settings.gradle\nsettings.gradle.kts",
    "build.gradle\nbuild.gradle.kts",
    "pom.xml",
    "build.xml",
    "CMakeLists.txt",
    "Makefile\nmakefile\nGNUmakefile",
    "*.ini",
    "*.proto",
    "AndroidManifest.xml",
    "*/res/*.xml\n*/res/**/*.xml",
    "go.mod\ngo.work",
    "Cargo.toml",
    "pubspec.yaml",
    "Package.swift\nproject.pbxproj",
    "*.csproj\n*.vbproj\n*.sln\n*.slnx",
    "pyproject.toml\nsetup.cfg\nsetup.py\nPipfile",
    "package.json\njsconfig.json\ntsconfig.json",
    "composer.json",
];

const DESCRIPTOR_SPECS_TAIL: [&str; 8] = [
    "cpanfile\nMETA.json\nMETA.yml\ndist.ini\nMakefile.PL\nBuild.PL",
    "*.dproj\n*.groupproj\n*.dpk\n*.dpr",
    "*.vbp\n*.vbg\n*.vbw",
    "dbt_project.yml\nliquibase*.xml\nflyway*.conf",
    "application*.properties\napplication*.yml\napplication*.yaml\nweb.xml\nstruts*.xml\nmybatis*.xml\nappsettings*.json",
    ".env\n.env.*\nsecrets.json\ngradle.properties",
    "*.lock\npackage-lock.json\nyarn.lock\npnpm-lock.yaml\ngo.sum\nPackage.resolved",
    "web.config\nGlobal.asax\n*.cshtml\n*.razor",
];

/// `descriptor_spec_for_path(...).is_some()` — fnmatch against the path and
/// its basename across every registered spec.
pub fn is_descriptor_path(path: &str) -> bool {
    let normalized = path.replace('\\', "/");
    let basename = normalized.rsplit('/').next().unwrap_or(&normalized).to_string();
    for group in DESCRIPTOR_SPECS.iter().chain(DESCRIPTOR_SPECS_TAIL.iter()) {
        for pattern in group.split('\n') {
            if util::fnmatch(&normalized, pattern) || util::fnmatch(&basename, pattern) {
                return true;
            }
        }
    }
    false
}

/// `is_jp1_file` — .txt sniffing with the legacy-encoding decode subset
/// (UTF-16 BOM/NUL heuristics + UTF-8; cp932-only content degrades to
/// lossy cp1252, which preserves the structural checks).
pub fn is_jp1_file(path: &Path) -> bool {
    let lower = path.to_string_lossy().to_lowercase();
    if !lower.ends_with(".txt") {
        return false;
    }
    let data = match std::fs::read(path) {
        Ok(data) => data,
        Err(_) => return false,
    };
    let text = decode_legacy(&data);
    let lines: Vec<&str> = text
        .lines()
        .map(|line| line.trim())
        .filter(|line| !line.is_empty())
        .collect();
    let has_unit = lines
        .iter()
        .take(5)
        .any(|line| {
            regex::Regex::new(r"(?i)^unit=").map(|re| re.is_match(line)).unwrap_or(false)
        });
    if !has_unit {
        return false;
    }
    let has_brace = lines.iter().take(10).any(|line| line.starts_with('{'));
    let has_ty = lines.iter().take(50).any(|line| {
        regex::Regex::new(r"(?i)^ty\s*=").map(|re| re.is_match(line)).unwrap_or(false)
    });
    has_brace && has_ty
}

fn decode_legacy(data: &[u8]) -> String {
    if data.starts_with(&[0xff, 0xfe]) {
        return decode_utf16(&data[2..], true);
    }
    if data.starts_with(&[0xfe, 0xff]) {
        return decode_utf16(&data[2..], false);
    }
    if data.starts_with(&[0xef, 0xbb, 0xbf]) {
        return String::from_utf8_lossy(&data[3..]).to_string();
    }
    let sample = &data[..data.len().min(512)];
    if !sample.is_empty() {
        let odd_nuls = sample.iter().skip(1).step_by(2).filter(|&&b| b == 0).count();
        let even_nuls = sample.iter().step_by(2).filter(|&&b| b == 0).count();
        let threshold = std::cmp::max(2, sample.len() / 8);
        if odd_nuls >= threshold {
            return decode_utf16(data, true);
        }
        if even_nuls >= threshold {
            return decode_utf16(data, false);
        }
    }
    match std::str::from_utf8(data) {
        Ok(text) => text.to_string(),
        Err(_) => data.iter().map(|&b| b as char).collect(),
    }
}

fn decode_utf16(data: &[u8], little_endian: bool) -> String {
    let units: Vec<u16> = data
        .chunks_exact(2)
        .map(|pair| {
            if little_endian {
                u16::from_le_bytes([pair[0], pair[1]])
            } else {
                u16::from_be_bytes([pair[0], pair[1]])
            }
        })
        .collect();
    String::from_utf16_lossy(&units)
}

fn file_suffix(path: &str) -> String {
    let lower = path.to_lowercase();
    match lower.rfind('.') {
        Some(index) if !path[index..].contains('/') => path[index..].to_lowercase(),
        _ => String::new(),
    }
}

/// `_walk_all_source_files` — recursive walk honoring `_SKIP_DIRS`, dot
/// directories, extra ignores, and the source/descriptor/jp1 file filter.
pub fn walk_all_source_files(root: &Path) -> BTreeSet<String> {
    let mut found = BTreeSet::new();
    let root_abs = crate::util::realpath(&crate::util::path_to_string(root));
    walk_recursive(&root_abs, &root_abs, &mut found);
    found
}

fn walk_recursive(base: &Path, dir: &Path, found: &mut BTreeSet<String>) {
    let read = match std::fs::read_dir(dir) {
        Ok(read) => read,
        Err(_) => return,
    };
    let mut subdirs: Vec<std::path::PathBuf> = Vec::new();
    for entry in read.flatten() {
        let path = entry.path();
        let Ok(file_type) = entry.file_type() else { continue };
        if file_type.is_dir() {
            subdirs.push(path);
            continue;
        }
        let name = entry.file_name().to_string_lossy().to_string();
        let lower = name.to_lowercase();
        let ext = file_suffix(&lower);
        let rel_full = match path.strip_prefix(base) {
            Ok(rel) => rel,
            Err(_) => continue,
        };
        let rel = crate::gitdiff::to_posix(&crate::util::path_to_string(rel_full));
        let is_source = SOURCE_EXTENSIONS.contains(&ext.as_str())
            || SOURCE_EXTENSIONS_TAIL.contains(&ext.as_str())
            || SOURCE_EXTENSIONS_TAIL2.contains(&ext.as_str())
            || lower.ends_with(".gradle.kts")
            || is_descriptor_path(&rel)
            || (ext == ".txt" && is_jp1_file(&path));
        if !is_source {
            continue;
        }
        found.insert(rel);
    }
    for subdir in subdirs {
        let name = subdir
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        if is_skip_dir(&name) || name.starts_with('.') || matches_extra_ignore(&name) {
            continue;
        }
        walk_recursive(base, &subdir, found);
    }
}

/// `_is_source_candidate` — the git-candidate filter used before inventory
/// intersection.
pub fn is_source_candidate(root: &Path, normalized: &str, summary_rel_path: Option<&str>) -> bool {
    if let Some(summary_rel) = summary_rel_path
        && normalized == summary_rel {
            return false;
        }
    let parts: Vec<&str> = normalized.split('/').collect();
    if parts.len() > 1 {
        for part in &parts[..parts.len() - 1] {
            if is_skip_dir(part) || part.starts_with('.') {
                return false;
            }
        }
    }
    let lower = normalized.to_lowercase();
    let ext = file_suffix(&lower);
    ext_is_source(&ext, &lower)
        || lower.ends_with(".gradle.kts")
        || is_descriptor_path(normalized)
        || (ext == ".txt" && is_jp1_file(&root.join(normalized)))
}

fn ext_is_source(ext: &str, lower: &str) -> bool {
    let _ = lower;
    SOURCE_EXTENSIONS.contains(&ext)
        || SOURCE_EXTENSIONS_TAIL.contains(&ext)
        || SOURCE_EXTENSIONS_TAIL2.contains(&ext)
}

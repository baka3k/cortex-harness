//! Port phần scan của `android_kotlin_analyzer.py`: `_ANDROID_SKIP_DIRS` của
//! analyzer (KHÔNG phải bản android_common — analyzer dùng set riêng),
//! `_scan_android_kotlin_files` / `_scan_android_manifest_files` /
//! `_scan_android_gradle_files` / `_scan_android_resource_xml_files`, import
//! graph theo package text và BFS impact expansion, `_parse_gradle_file`,
//! `_extract_resource_refs`, `_extract_android_annotations`, `_parse_compose_routes`.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use cortex_analyzer_framework::scan::matches_extra_ignore;

/// `_ANDROID_SKIP_DIRS` của android_kotlin_analyzer.py (bản riêng của analyzer).
pub const ANDROID_SKIP_DIRS: [&str; 44] = [
    // Version control
    ".git",
    ".hg",
    ".svn",
    // Gradle
    ".gradle",
    "gradleCache",
    ".mvn",
    // IDE
    ".idea",
    ".vscode",
    ".settings",
    ".capture",
    // Android specific
    ".android",
    ".cxx",
    ".externalNativeBuild",
    // Build outputs
    "build",
    "out",
    "bin",
    "dist",
    "buildSrc",
    // Generated
    "gen",
    "generated",
    // Node (mixed projects)
    "node_modules",
    // Cache
    ".cache",
    ".parcel-cache",
    "__pycache__",
    // Testing
    "coverage",
    ".test-results",
    "test-results",
    "androidTest",
    // Lint
    "lint-results",
    "lint-baseline.xml",
    // Temporary
    "tmp",
    "temp",
    ".tmp",
    "tmpdir",
    // OS specific
    ".DS_Store",
    "Thumbs.db",
    // Misc project files
    ".project",
    ".classpath",
    "*.iml",
    "*.ipr",
    "*.iws",
    // APK/AAB outputs
    "*.apk",
    "*.aab",
    "*.ap_",
];

/// fnmatchcase cho pattern `*`/`?` (các pattern trong skip-set không dùng
/// `[seq]`; POSIX normcase là identity nên so khớp case-sensitive).
fn fnmatch_case(name: &str, pattern: &str) -> bool {
    fn inner(name: &[u8], pattern: &[u8]) -> bool {
        match (pattern.first(), name.first()) {
            (None, None) => true,
            (Some(b'*'), _) => {
                for skip in 0..=name.len() {
                    if inner(&name[skip..], &pattern[1..]) {
                        return true;
                    }
                }
                false
            }
            (Some(b'?'), Some(_)) => inner(&name[1..], &pattern[1..]),
            (Some(p), Some(c)) if p == c => inner(&name[1..], &pattern[1..]),
            _ => false,
        }
    }
    inner(name.as_bytes(), pattern.as_bytes())
}

/// `_is_skipped_android_name`.
pub fn is_skipped_android_name(name: &str) -> bool {
    if name.starts_with('.') {
        return true;
    }
    for pattern in ANDROID_SKIP_DIRS {
        if name == pattern || fnmatch_case(name, pattern) {
            return true;
        }
    }
    matches_extra_ignore(name)
}

/// `rel_posix` — os.path.relpath theo POSIX (file scanned luôn dưới root).
pub fn rel_path_posix(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .components()
        .map(|c| c.as_os_str().to_string_lossy().to_string())
        .collect::<Vec<_>>()
        .join("/")
}

/// Walk cả tree với skip-filter áp cho dirnames lẫn filenames.
fn walk_files(root: &Path) -> Vec<(PathBuf, String)> {
    let mut out: Vec<(PathBuf, String)> = Vec::new();
    let mut stack: Vec<PathBuf> = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let entries = match std::fs::read_dir(&dir) {
            Ok(entries) => entries,
            Err(_) => continue,
        };
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            let path = entry.path();
            if path.is_dir() {
                if !is_skipped_android_name(&name) {
                    stack.push(path);
                }
                continue;
            }
            if !is_skipped_android_name(&name) {
                let rel = rel_path_posix(root, &path);
                out.push((path, rel));
            }
        }
    }
    out
}

/// `_scan_android_kotlin_files` — .kt/.kts, sorted theo abs path (như Python
/// `sorted()` trên chuỗi absolute path).
pub fn scan_android_kotlin_files(root: &Path) -> Vec<PathBuf> {
    let mut files: Vec<String> = walk_files(root)
        .into_iter()
        .filter(|(path, _)| {
            let name = path
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();
            name.ends_with(".kt") || name.ends_with(".kts")
        })
        .map(|(path, _)| path.to_string_lossy().to_string())
        .collect();
    files.sort();
    files.into_iter().map(PathBuf::from).collect()
}

/// `_scan_android_manifest_files` — tên đúng `AndroidManifest.xml`, sorted.
pub fn scan_android_manifest_files(root: &Path) -> Vec<PathBuf> {
    let mut files: Vec<String> = walk_files(root)
        .into_iter()
        .filter(|(path, _)| {
            path.file_name().map(|n| n == std::ffi::OsStr::new("AndroidManifest.xml")).unwrap_or(false)
        })
        .map(|(path, _)| path.to_string_lossy().to_string())
        .collect();
    files.sort();
    files.into_iter().map(PathBuf::from).collect()
}

/// `_scan_android_gradle_files` — `build.gradle` / `build.gradle.kts`, sorted.
pub fn scan_android_gradle_files(root: &Path) -> Vec<PathBuf> {
    let mut files: Vec<String> = walk_files(root)
        .into_iter()
        .filter(|(path, _)| {
            let name = path
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();
            name == "build.gradle" || name == "build.gradle.kts"
        })
        .map(|(path, _)| path.to_string_lossy().to_string())
        .collect();
    files.sort();
    files.into_iter().map(PathBuf::from).collect()
}

/// `_scan_android_resource_xml_files` — chỉ file .xml dưới một thư mục `res/`
/// (kiểm tra chuỗi `{sep}res{sep}` trên dirpath + trailing sep như Python).
pub fn scan_android_resource_xml_files(root: &Path) -> Vec<PathBuf> {
    let mut files: Vec<String> = walk_files(root)
        .into_iter()
        .filter(|(path, _)| {
            let name = path
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();
            if !name.ends_with(".xml") {
                return false;
            }
            let parent = match path.parent() {
                Some(parent) => parent.to_string_lossy().to_string(),
                None => String::new(),
            };
            format!("/{parent}/").contains("/res/")
        })
        .map(|(path, _)| path.to_string_lossy().to_string())
        .collect();
    files.sort();
    files.into_iter().map(PathBuf::from).collect()
}

/// `_extract_kotlin_package_and_imports_from_text` — regex theo dòng.
pub fn extract_kotlin_package_and_imports_from_text(text: &str) -> (Option<String>, Vec<String>) {
    static PKG_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    static IMP_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let pkg_re = PKG_RE.get_or_init(|| regex::Regex::new(r"^package\s+([A-Za-z_][A-Za-z0-9_\.]*)\s*$").unwrap());
    let imp_re = IMP_RE.get_or_init(|| {
        regex::Regex::new(r"^import\s+([A-Za-z_][A-Za-z0-9_\.]*)(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?\s*$")
            .unwrap()
    });

    let mut package_name: Option<String> = None;
    let mut imports: Vec<String> = Vec::new();
    for raw_line in text.lines() {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with("//") {
            continue;
        }
        if package_name.is_none()
            && let Some(caps) = pkg_re.captures(line)
        {
            package_name = Some(caps[1].to_string());
            continue;
        }
        if let Some(caps) = imp_re.captures(line) {
            imports.push(caps[1].to_string());
        }
    }
    (package_name, imports)
}

/// `_collect_kotlin_import_graph` — rel → sorted deps qua package match.
pub fn collect_kotlin_import_graph(all_kotlin_files: &[PathBuf], root: &Path) -> HashMap<String, Vec<String>> {
    // package → files: dict Python giữ thứ tự chèn; dùng insertion-order vec +
    // set cho lookup.
    let mut package_order: Vec<String> = Vec::new();
    let mut package_to_files: HashMap<String, std::collections::BTreeSet<String>> = HashMap::new();
    let mut rel_paths: Vec<String> = Vec::new();
    let mut imports_by_file: HashMap<String, Vec<String>> = HashMap::new();

    for abs_path in all_kotlin_files {
        let rel_path = rel_path_posix(root, abs_path);
        rel_paths.push(rel_path.clone());
        let text = match std::fs::read(abs_path) {
            Ok(bytes) => cortex_analyzer_framework::ts::decode_ignore(&bytes),
            Err(_) => {
                imports_by_file.insert(rel_path, Vec::new());
                continue;
            }
        };
        let (package_name, imports) = extract_kotlin_package_and_imports_from_text(&text);
        imports_by_file.insert(rel_path.clone(), imports);
        if let Some(package_name) = package_name {
            if !package_to_files.contains_key(&package_name) {
                package_order.push(package_name.clone());
            }
            package_to_files
                .entry(package_name)
                .or_default()
                .insert(rel_path);
        }
    }

    let mut deps_by_file: HashMap<String, Vec<String>> = HashMap::new();
    for rel_path in &rel_paths {
        let mut resolved: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
        for imp in imports_by_file.get(rel_path).into_iter().flatten() {
            for package_name in &package_order {
                let matched = imp == package_name
                    || imp.starts_with(&format!("{package_name}."));
                if matched
                    && let Some(files) = package_to_files.get(package_name)
                {
                    resolved.extend(files.iter().cloned());
                }
            }
        }
        resolved.remove(rel_path);
        deps_by_file.insert(rel_path.clone(), resolved.into_iter().collect());
    }
    deps_by_file
}

/// `_expand_impacted_files_by_imports` — BFS FIFO qua reverse map sorted.
pub fn expand_impacted_files_by_imports(
    changed_existing: &HashSet<String>,
    deps_by_file: &HashMap<String, Vec<String>>,
) -> HashSet<String> {
    let mut reverse_map: HashMap<String, std::collections::BTreeSet<String>> = HashMap::new();
    for (source, deps) in deps_by_file {
        for dep in deps {
            reverse_map.entry(dep.clone()).or_default().insert(source.clone());
        }
    }

    let mut impacted: HashSet<String> = HashSet::new();
    let mut queue: std::collections::VecDeque<String> = changed_existing.iter().cloned().collect();
    let mut seen: HashSet<String> = changed_existing.clone();
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

// ── Gradle ──────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct GradleModuleDef {
    pub symbol_id: String,
    pub name: String,
    pub module_path: String,
    pub module_type: String,
    pub namespace: Option<String>,
    pub application_id: Option<String>,
    pub file_path: String,
}

#[derive(Debug, Clone)]
pub struct GradleDependencyDef {
    pub symbol_id: String,
    pub coordinate: String,
    pub group: Option<String>,
    pub artifact: Option<String>,
    pub version: Option<String>,
}

/// `_parse_gradle_file` — regex gốc dùng raw-string `\\s` (tức backslash
/// literal trước `s*`) nên namespace/applicationId/dependency thực tế KHÔNG
/// match file thường. Port giữ đúng semantics này (byte-parity).
pub fn parse_gradle_file(
    path: &Path,
    root: &Path,
) -> (GradleModuleDef, Vec<GradleDependencyDef>, Vec<(String, String, String)>) {
    static NS_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    static APPID_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    static DEP_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let ns_re = NS_RE.get_or_init(|| regex::Regex::new(r#"(?m)^\\s*namespace\\s*=?\\s*[\"']([^\"']+)[\"']"#).unwrap());
    let appid_re = APPID_RE
        .get_or_init(|| regex::Regex::new(r#"(?m)^\\s*applicationId\\s*=?\\s*[\"']([^\"']+)[\"']"#).unwrap());
    let dep_re = DEP_RE.get_or_init(|| {
        regex::Regex::new(r#"(?m)^\\s*(\\w+)\\s*\\(\\s*[\"']([^\"']+)[\"']\\s*\\)"#).unwrap()
    });

    let rel_path = rel_path_posix(root, path);
    let module_dir_abs = path.parent().unwrap_or(root);
    let module_dir = rel_path_posix(root, module_dir_abs);
    let module_path = if module_dir == "." || module_dir.is_empty() {
        ":".to_string()
    } else {
        format!(":{module_dir}")
    };
    let module_name = if module_dir == "." || module_dir.is_empty() {
        "root".to_string()
    } else {
        module_dir.clone()
    };
    let content = match std::fs::read(path) {
        Ok(bytes) => cortex_analyzer_framework::ts::decode_ignore(&bytes),
        Err(_) => String::new(),
    };

    let module_type = if content.contains("com.android.application") {
        "app"
    } else if content.contains("com.android.library") {
        "library"
    } else {
        "unknown"
    };

    let namespace = ns_re.captures(&content).map(|caps| caps[1].to_string());
    let application_id = appid_re.captures(&content).map(|caps| caps[1].to_string());

    let module_def = GradleModuleDef {
        symbol_id: crate::common::module_symbol_id(&module_path),
        name: module_name,
        module_path,
        module_type: module_type.to_string(),
        namespace,
        application_id,
        file_path: rel_path,
    };

    let mut dependencies: Vec<GradleDependencyDef> = Vec::new();
    let mut dep_edges: Vec<(String, String, String)> = Vec::new();
    for caps in dep_re.captures_iter(&content) {
        let config = &caps[1];
        let coordinate = caps[2].trim().to_string();
        if !coordinate.contains(':') || coordinate.starts_with("project(") {
            continue;
        }
        let parts: Vec<&str> = coordinate.split(':').collect();
        let group = parts.first().copied().map(str::to_string);
        let artifact = parts.get(1).copied().map(str::to_string);
        let version = parts.get(2).copied().map(str::to_string);
        let dep_id = crate::common::dependency_symbol_id(&coordinate);
        dependencies.push(GradleDependencyDef {
            symbol_id: dep_id.clone(),
            coordinate,
            group,
            artifact,
            version,
        });
        dep_edges.push((module_def.symbol_id.clone(), dep_id, config.to_string()));
    }
    (module_def, dependencies, dep_edges)
}

// ── Resource refs + annotations ─────────────────────────────────────────────

/// `_extract_resource_refs` — regex gốc double-backslash nên chỉ match khi
/// code chứa literal backslash; port giữ nguyên semantics.
pub fn extract_resource_refs(code: &str) -> Vec<(String, String)> {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| {
        regex::Regex::new(
            r"(?:\\b[A-Za-z0-9_\\.]+\\.)?R\\.(layout|id|string|drawable|navigation|menu|color|anim|mipmap|raw|font|xml)\\.([A-Za-z0-9_]+)",
        )
        .unwrap()
    });
    let mut refs = Vec::new();
    for caps in re.captures_iter(code) {
        refs.push((caps[1].to_string(), caps[2].to_string()));
    }
    refs
}

pub const ANDROID_ANNOTATIONS: [&str; 24] = [
    "HiltAndroidApp",
    "AndroidEntryPoint",
    "InstallIn",
    "Module",
    "Provides",
    "Binds",
    "Inject",
    "AssistedInject",
    "AssistedFactory",
    "Singleton",
    "Qualifier",
    "Entity",
    "Dao",
    "Database",
    "Query",
    "Insert",
    "Update",
    "Delete",
    "Transaction",
    "TypeConverter",
    "Embedded",
    "Relation",
    "Parcelize",
    "Composable",
];

/// `_extract_android_annotations` — @Name match trong bộ annotation nổi tiếng.
pub fn extract_android_annotations(text: &str) -> Vec<String> {
    if text.is_empty() {
        return Vec::new();
    }
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| regex::Regex::new(r"@\s*([A-Za-z_][A-Za-z0-9_]*)").unwrap());
    re.captures_iter(text)
        .map(|caps| caps[1].to_string())
        .filter(|name| ANDROID_ANNOTATIONS.contains(&name.as_str()))
        .collect()
}

// ── Resources ───────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct ResourceDef {
    pub symbol_id: String,
    pub name: String,
    pub res_type: String,
    pub file_path: String,
    pub qualifier: String,
    pub summary: String,
    pub note: String,
}

/// `_extract_resource_ids_from_xml` — pattern Python là regex broken
/// (`@\\+?id/` = @ + backslash literal) nên findall luôn rỗng dù iterparse
/// chạy; kết quả quan sát được: luôn `[]`.
/// Port giữ kết quả tương đương.
fn extract_resource_ids_from_xml(_path: &Path) -> Vec<String> {
    Vec::new()
}

/// `_collect_android_resources` — (resources, index (res_type, name) → id);
/// candidates đã là danh sách res/xml đã scan (None ⇒ tự scan).
pub fn collect_android_resources(
    root: &Path,
    xml_files: &[PathBuf],
) -> (Vec<ResourceDef>, HashMap<(String, String), String>) {
    let mut resources: Vec<ResourceDef> = Vec::new();
    let mut resource_index: HashMap<(String, String), String> = HashMap::new();
    let candidates: Vec<&PathBuf> = xml_files.iter().collect();
    for path in candidates {
        let rel_path = rel_path_posix(root, path);
        let dir_name = path
            .parent()
            .and_then(|p| p.file_name())
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        let (res_type, qualifier) = match dir_name.split_once('-') {
            Some((res_type, qualifier)) => (res_type.to_string(), qualifier.to_string()),
            None => (dir_name.clone(), String::new()),
        };
        if !matches!(res_type.as_str(), "layout" | "navigation" | "menu") {
            continue;
        }
        let base_name = path
            .file_stem()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        let res_id = crate::common::resource_symbol_id(&res_type, &base_name);
        let key = (res_type.clone(), base_name.clone());
        if let std::collections::hash_map::Entry::Vacant(entry) = resource_index.entry(key) {
            entry.insert(res_id.clone());
            resources.push(ResourceDef {
                symbol_id: res_id,
                name: base_name,
                res_type,
                file_path: rel_path.clone(),
                qualifier: qualifier.clone(),
                summary: String::new(),
                note: String::new(),
            });
        }
        for view_id in extract_resource_ids_from_xml(path) {
            let id_key = ("id".to_string(), view_id.clone());
            if resource_index.contains_key(&id_key) {
                continue;
            }
            let view_res_id = crate::common::resource_symbol_id("id", &view_id);
            resource_index.insert(id_key, view_res_id.clone());
            resources.push(ResourceDef {
                symbol_id: view_res_id,
                name: view_id,
                res_type: "id".to_string(),
                file_path: rel_path.clone(),
                qualifier: qualifier.clone(),
                summary: String::new(),
                note: String::new(),
            });
        }
    }
    (resources, resource_index)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn skip_names_match_python_set() {
        assert!(is_skipped_android_name("build"));
        assert!(is_skipped_android_name("foo.iml"));
        assert!(is_skipped_android_name("app.apk"));
        assert!(is_skipped_android_name(".git"));
        assert!(!is_skipped_android_name("MainActivity.kt"));
        assert!(!is_skipped_android_name("builder")); // fnmatch "build" không match prefix
    }

    #[test]
    fn annotations_filter() {
        assert_eq!(
            extract_android_annotations("@Composable\n@Preview fun x()"),
            vec!["Composable".to_string()]
        );
        assert!(extract_android_annotations("@Deprecated fun x()").is_empty());
    }
}

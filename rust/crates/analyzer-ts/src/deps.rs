//! Port `tools/ts/agents/dependency_agent.py` — import graph + impacted expand.

use std::collections::{BTreeMap, BTreeSet, VecDeque};

pub const TS_SOURCE_EXTENSIONS: [&str; 4] = [".ts", ".tsx", ".mts", ".cts"];

/// `_extract_module_specifiers_from_text`.
pub fn extract_module_specifiers_from_text(text: &str) -> Vec<String> {
    let import_re = regex::Regex::new(
        r#"^(?:import|export)\s+(?:.+?\s+from\s+)?["'](?P<spec>[^"']+)["']"#,
    )
    .unwrap();
    let require_re = regex::Regex::new(
        r#"(?:require|import)\(\s*["'](?P<spec>[^"']+)["']\s*\)"#,
    )
    .unwrap();
    let mut specifiers = Vec::new();
    for raw_line in crate::pipeline::py_splitlines(text) {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with("//") || line.starts_with("/*") || line.starts_with("*")
        {
            continue;
        }
        if let Some(caps) = import_re.captures(line) {
            specifiers.push(caps.name("spec").unwrap().as_str().to_string());
        }
        for caps in require_re.captures_iter(line) {
            specifiers.push(caps.name("spec").unwrap().as_str().to_string());
        }
    }
    specifiers
}

/// `_resolve_ts_module_specifier`.
pub fn resolve_ts_module_specifier(
    source_rel_path: &str,
    specifier: &str,
    file_set: &BTreeSet<String>,
) -> Option<String> {
    if specifier.is_empty() || !specifier.starts_with('.') {
        return None;
    }
    let base_dir = match source_rel_path.rfind('/') {
        Some(pos) => &source_rel_path[..pos],
        None => "",
    };
    let joined = if base_dir.is_empty() {
        specifier.to_string()
    } else {
        format!("{base_dir}/{specifier}")
    };
    let candidate = normalize_path(&joined);
    if file_set.contains(&candidate) {
        return Some(candidate);
    }
    let (root_candidate, ext) = split_ext(&candidate);
    let mut probes: Vec<String> = Vec::new();
    if !ext.is_empty() {
        probes.push(candidate.clone());
    } else {
        probes.extend(TS_SOURCE_EXTENSIONS.map(|suffix| format!("{candidate}{suffix}")));
    }
    probes.push(format!("{candidate}.d.ts"));
    probes.extend(TS_SOURCE_EXTENSIONS.map(|suffix| format!("{candidate}/index{suffix}")));
    probes.push(format!("{candidate}/index.d.ts"));
    for path in probes {
        let normalized = normalize_path(&path);
        if file_set.contains(&normalized) {
            return Some(normalized);
        }
    }
    if matches!(ext.as_str(), ".ts" | ".tsx" | ".mts" | ".cts") {
        return None;
    }
    if ext.is_empty() {
        for fallback_ext in [".ts", ".tsx", ".d.ts"] {
            let normalized = normalize_path(&format!("{root_candidate}{fallback_ext}"));
            if file_set.contains(&normalized) {
                return Some(normalized);
            }
        }
    }
    None
}

/// os.path.normpath với '/' — lexical, giữ leading '..'.
fn normalize_path(path: &str) -> String {
    let mut out: Vec<&str> = Vec::new();
    for segment in path.split('/') {
        match segment {
            "." => {}
            ".." => {
                if !out.is_empty() && *out.last().unwrap() != ".." {
                    out.pop();
                } else {
                    out.push("..");
                }
            }
            other => out.push(other),
        }
    }
    out.join("/")
}

fn split_ext(path: &str) -> (String, String) {
    let file_name = path.rsplit('/').next().unwrap_or(path);
    match file_name.rfind('.') {
        Some(pos) => (
            path[..path.len() - (file_name.len() - pos)].to_string(),
            file_name[pos..].to_string(),
        ),
        None => (path.to_string(), String::new()),
    }
}

/// `_collect_ts_import_graph` — {rel_path: sorted deps}.
pub fn collect_ts_import_graph(
    all_ts_files: &[std::path::PathBuf],
    root: &std::path::Path,
) -> BTreeMap<String, Vec<String>> {
    let rel_paths: Vec<String> = all_ts_files
        .iter()
        .map(|path| cortex_analyzer_framework::scan::rel_posix(root, path))
        .collect();
    let file_set: BTreeSet<String> = rel_paths.iter().cloned().collect();
    let mut deps_by_file: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (abs_path, rel_path) in all_ts_files.iter().zip(&rel_paths) {
        let text = match std::fs::read_to_string(abs_path) {
            Ok(text) => text,
            Err(_) => {
                deps_by_file.insert(rel_path.clone(), Vec::new());
                continue;
            }
        };
        let mut resolved: BTreeSet<String> = BTreeSet::new();
        for specifier in extract_module_specifiers_from_text(&text) {
            if let Some(dep) = resolve_ts_module_specifier(rel_path, &specifier, &file_set) {
                resolved.insert(dep);
            }
        }
        resolved.remove(rel_path);
        deps_by_file.insert(rel_path.clone(), resolved.into_iter().collect());
    }
    deps_by_file
}

/// `_expand_impacted_files_by_imports` — BFS reverse deps.
pub fn expand_impacted_files_by_imports(
    changed_existing: &BTreeSet<String>,
    deps_by_file: &BTreeMap<String, Vec<String>>,
) -> BTreeSet<String> {
    let mut reverse_map: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
    for (source, deps) in deps_by_file {
        for dep in deps {
            reverse_map.entry(dep.as_str()).or_default().insert(source.as_str());
        }
    }
    let mut impacted: BTreeSet<String> = BTreeSet::new();
    let mut queue: VecDeque<&str> = changed_existing.iter().map(String::as_str).collect();
    let mut seen: BTreeSet<&str> = changed_existing.iter().map(String::as_str).collect();
    while let Some(current) = queue.pop_front() {
        if let Some(dependents) = reverse_map.get(current) {
            for dependent in dependents {
                if seen.contains(dependent) {
                    continue;
                }
                seen.insert(dependent);
                impacted.insert(dependent.to_string());
                queue.push_back(dependent);
            }
        }
    }
    impacted
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn module_resolution_probes() {
        let mut file_set = BTreeSet::new();
        file_set.insert("src/screens/Login.tsx".to_string());
        file_set.insert("src/components/Button/index.tsx".to_string());
        file_set.insert("src/api.d.ts".to_string());
        assert_eq!(
            resolve_ts_module_specifier("src/screens/Home.tsx", "./Login", &file_set),
            Some("src/screens/Login.tsx".to_string())
        );
        assert_eq!(
            resolve_ts_module_specifier("src/App.tsx", "./components/Button", &file_set),
            Some("src/components/Button/index.tsx".to_string())
        );
        assert_eq!(
            resolve_ts_module_specifier("src/App.tsx", "react", &file_set),
            None
        );
        assert_eq!(
            resolve_ts_module_specifier("src/App.tsx", "./api", &file_set),
            Some("src/api.d.ts".to_string())
        );
        assert_eq!(normalize_path("src/a/../b"), "src/b");
        assert_eq!(normalize_path("../outside/x"), "../outside/x");
    }
}

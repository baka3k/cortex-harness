//! `_group_paths_by_framework` port — extension-candidate gating, strong
//! deleted-candidate rules, struts/flutter build-file evidence walks, web
//! framework content markers, and database dialect routing.
//!
//! Documented divergence (Python-plane): the Java-side module detectors
//! (`SpringProjectDetector`, `ServletJspProjectDetector`,
//! `MyBatisProjectDetector`, `AspNetFrameworkDetector`, `AspNetCoreDetector`)
//! and `detect_flutter_project` are not ported. Their module-routing and
//! evidence collection degrade to the strong-candidate rules below. Runs
//! whose parser filter includes a framework overlay that needs module-level
//! detection are delegated to the Python orchestrator (see orchestrator).

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use crate::registry;
use crate::util;
use crate::walk;

pub fn framework_candidate_extensions() -> BTreeMap<&'static str, Vec<&'static str>> {
    BTreeMap::from([
        ("spring", vec![".java", ".kt", ".kts", ".xml", ".properties", ".yml", ".yaml", ".json", ".gradle"]),
        ("servlet_jsp", vec![".java", ".jsp", ".jspx", ".jspf", ".tag", ".tagx", ".xml", ".properties", ".gradle"]),
        ("mybatis", vec![".java", ".kt", ".kts", ".xml", ".gradle"]),
        ("struts", vec![".java", ".xml", ".properties", ".yml", ".yaml", ".gradle"]),
        (
            "flutter",
            vec![
                ".dart", ".arb", ".json", ".xml", ".plist", ".gradle", ".properties", ".yml", ".yaml",
                ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ttf", ".otf",
            ],
        ),
        (
            "aspnet_framework",
            vec![".cs", ".csproj", ".sln", ".config", ".asax", ".aspx", ".ascx", ".master", ".asmx", ".ashx", ".cshtml", ".resx"],
        ),
        ("aspnet_core", vec![".cs", ".csproj", ".sln", ".cshtml", ".razor", ".json", ".config"]),
        ("fastapi_django", vec![".py"]),
        ("express_js", vec![".js", ".jsx"]),
        ("laravel", vec![".php"]),
        ("database_sql", vec![".sql", ".ddl", ".dml", ".psql"]),
        ("database_plsql", vec![".pls", ".plsql", ".pks", ".pkb", ".pkg", ".pck", ".spc", ".spb", ".trg", ".fnc"]),
    ])
}

pub fn framework_build_files() -> BTreeSet<&'static str> {
    BTreeSet::from([
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    ])
}

fn file_suffix(path: &str) -> String {
    let lower = path.to_lowercase();
    let basename = lower.rsplit('/').next().unwrap_or(&lower);
    match basename.rfind('.') {
        Some(index) => basename[index..].to_string(),
        None => String::new(),
    }
}

fn basename_lower(path: &str) -> String {
    let lower = path.replace('\\', "/").to_lowercase();
    lower.rsplit('/').next().unwrap_or(&lower).to_string()
}

/// `_is_framework_candidate`.
pub fn is_framework_candidate(framework: &str, path: &str) -> bool {
    let extensions = framework_candidate_extensions();
    let Some(allowed) = extensions.get(framework) else {
        return false;
    };
    let name = basename_lower(path);
    let ext = file_suffix(path);
    framework_build_files().contains(name.as_str()) || allowed.contains(&ext.as_str())
}

/// `_path_in_module`.
#[allow(dead_code)]
pub fn path_in_module(path: &str, module_root: &str) -> bool {
    let normalized = path.replace('\\', "/").trim_matches('/').to_string();
    let module = (module_root.trim()).replace('\\', "/");
    let module = module.trim_matches('/').to_string();
    module.is_empty() || module == "." || normalized == module || normalized.starts_with(&format!("{module}/"))
}

fn read_limited_lower(path: &Path, limit: usize) -> String {
    std::fs::read(path)
        .map(|bytes| String::from_utf8_lossy(&bytes[..bytes.len().min(limit)]).to_lowercase())
        .unwrap_or_default()
}

pub struct FrameworkRouting {
    pub grouped: BTreeMap<String, BTreeSet<String>>,
    pub evidence: BTreeMap<String, Vec<String>>,
}

/// `_group_paths_by_framework` (detectors-lite). Module detectors are not
/// ported; strong-candidate rules carry deleted artifacts and obvious names.
pub fn group_paths_by_framework(paths: &BTreeSet<String>, root: &Path) -> FrameworkRouting {
    let mut grouped: BTreeMap<String, BTreeSet<String>> = registry::framework_analyzers()
        .keys()
        .map(|name| (name.to_string(), BTreeSet::new()))
        .collect();
    let mut evidence: BTreeMap<String, Vec<String>> = registry::framework_analyzers()
        .keys()
        .map(|name| (name.to_string(), Vec::new()))
        .collect();
    let normalized_paths: BTreeSet<String> =
        paths.iter().filter(|p| !p.is_empty()).map(|p| p.replace('\\', "/")).collect();
    if normalized_paths.is_empty() {
        return FrameworkRouting { grouped, evidence };
    }

    // Java-side module-detector lanes (spring/servlet_jsp/mybatis) degrade to
    // candidate gating + strong names (deleted artifacts still route).
    for framework in ["spring", "servlet_jsp", "mybatis"] {
        let candidates: BTreeSet<String> = normalized_paths
            .iter()
            .filter(|path| is_framework_candidate(framework, path))
            .cloned()
            .collect();
        if candidates.is_empty() {
            continue;
        }
        for path in &candidates {
            let name = basename_lower(path);
            let strong = match framework {
                "mybatis" => name.ends_with("mapper.xml") || name.contains("mybatis"),
                "servlet_jsp" => {
                    name == "web.xml"
                        || name.ends_with(".jsp")
                        || name.ends_with(".jspx")
                        || name.ends_with(".jspf")
                        || name.ends_with(".tag")
                        || name.ends_with(".tagx")
                }
                "spring" => {
                    name.starts_with("application")
                        && (name.ends_with(".properties") || name.ends_with(".yml") || name.ends_with(".yaml"))
                }
                _ => false,
            };
            if strong {
                grouped.entry(framework.to_string()).or_default().insert(path.clone());
                evidence
                    .entry(framework.to_string())
                    .or_default()
                    .push(format!("{path}:strong-candidate"));
            }
        }
    }

    // struts: repo walk for struts-*.xml configs and build-file dependencies.
    let struts_candidates: BTreeSet<String> = normalized_paths
        .iter()
        .filter(|path| is_framework_candidate("struts", path))
        .cloned()
        .collect();
    let struts_evidence = struts_evidence_walk(root);
    if !struts_evidence.is_empty() {
        grouped.entry("struts".to_string()).or_default().extend(struts_candidates.iter().cloned());
        evidence.insert("struts".to_string(), struts_evidence.clone());
    } else {
        let strong: BTreeSet<String> = struts_candidates
            .iter()
            .filter(|path| {
                let name = basename_lower(path);
                name == "struts.xml"
                    || name == "struts-plugin.xml"
                    || name.ends_with("-validation.xml")
            })
            .cloned()
            .collect();
        grouped.entry("struts".to_string()).or_default().extend(strong.iter().cloned());
        evidence.insert(
            "struts".to_string(),
            strong.iter().map(|path| format!("{path}:strong-candidate")).collect(),
        );
    }

    // flutter: pubspec.yaml detection (full detector remains Python-plane).
    let flutter_candidates: BTreeSet<String> = normalized_paths
        .iter()
        .filter(|path| is_framework_candidate("flutter", path))
        .cloned()
        .collect();
    if root.join("pubspec.yaml").is_file() {
        grouped.entry("flutter".to_string()).or_default().extend(flutter_candidates.iter().cloned());
    } else {
        let strong: BTreeSet<String> = flutter_candidates
            .iter()
            .filter(|path| basename_lower(path) == "pubspec.yaml")
            .cloned()
            .collect();
        grouped.entry("flutter".to_string()).or_default().extend(strong.iter().cloned());
        evidence.insert(
            "flutter".to_string(),
            strong.iter().map(|path| format!("{path}:strong-candidate")).collect(),
        );
    }

    // aspnet detectors degrade to strong deleted candidates.
    for framework in ["aspnet_framework", "aspnet_core"] {
        let candidates: BTreeSet<String> = normalized_paths
            .iter()
            .filter(|path| is_framework_candidate(framework, path))
            .cloned()
            .collect();
        for path in &candidates {
            if !root.join(path).exists() {
                grouped.entry(framework.to_string()).or_default().insert(path.clone());
                evidence
                    .entry(framework.to_string())
                    .or_default()
                    .push(format!("{path}:strong-candidate"));
            }
        }
    }

    // Web framework content markers.
    type WebSpec = (&'static str, Vec<&'static str>, Vec<&'static str>, Vec<&'static str>);
    let web_specs: Vec<WebSpec> = vec![
        (
            "fastapi_django",
            vec![".py"],
            vec!["fastapi", "django.", "django ", "urlpatterns", "@app.get", "@router.get"],
            vec!["urls.py"],
        ),
        (
            "express_js",
            vec![".js", ".jsx"],
            vec!["express", "app.get(", "router.get(", "app.post(", "router.post("],
            vec!["routes.js", "router.js"],
        ),
        (
            "laravel",
            vec![".php"],
            vec!["illuminate\\", "route::", "extends controller"],
            vec!["routes.php", "web.php", "api.php"],
        ),
    ];
    for (framework, extensions, markers, strong_names) in web_specs {
        let candidates: BTreeSet<String> = normalized_paths
            .iter()
            .filter(|path| extensions.contains(&file_suffix(path).as_str()))
            .cloned()
            .collect();
        let mut detected_evidence: Vec<String> = Vec::new();
        for path in &candidates {
            let absolute = root.join(path);
            if !absolute.is_file() {
                if strong_names.contains(&basename_lower(path).as_str()) {
                    detected_evidence.push(format!("{path}:strong-candidate"));
                }
                continue;
            }
            let content = read_limited_lower(&absolute, usize::MAX);
            if markers.iter().any(|marker| content.contains(marker)) {
                detected_evidence.push(format!("{path}:{framework}"));
            }
        }
        if !detected_evidence.is_empty() {
            grouped.entry(framework.to_string()).or_default().extend(candidates.iter().cloned());
            evidence.insert(framework.to_string(), detected_evidence);
        }
    }

    // Database dialect routing is pure extension matching.
    for framework in ["database_sql", "database_plsql"] {
        let candidates: BTreeSet<String> = normalized_paths
            .iter()
            .filter(|path| {
                framework_candidate_extensions()
                    .get(framework)
                    .map(|extensions| extensions.contains(&file_suffix(path).as_str()))
                    .unwrap_or(false)
            })
            .cloned()
            .collect();
        grouped.entry(framework.to_string()).or_default().extend(candidates.iter().cloned());
        evidence.insert(
            framework.to_string(),
            candidates.iter().map(|path| format!("{path}:{framework}")).collect(),
        );
    }

    // Deduplicate evidence lists preserving order.
    for (framework, items) in evidence.iter_mut() {
        let mut seen = BTreeSet::new();
        items.retain(|item| seen.insert(item.clone()));
        let _ = framework;
    }
    FrameworkRouting { grouped, evidence }
}

fn struts_evidence_walk(root: &Path) -> Vec<String> {
    let mut evidence = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(read) = std::fs::read_dir(&dir) else { continue };
        for entry in read.flatten() {
            let Ok(file_type) = entry.file_type() else { continue };
            let name = entry.file_name().to_string_lossy().to_string();
            let path = entry.path();
            if file_type.is_dir() {
                if walk::is_skip_dir(&name) || name.starts_with('.') || walk::matches_extra_ignore(&name) {
                    continue;
                }
                stack.push(path);
                continue;
            }
            let lower = name.to_lowercase();
            let rel = match path.strip_prefix(root) {
                Ok(rel) => crate::gitdiff::to_posix(&util::path_to_string(rel)),
                Err(_) => continue,
            };
            if lower == "struts.xml" || lower == "struts-plugin.xml" || (lower.starts_with("struts-") && lower.ends_with(".xml")) {
                evidence.push(format!("{rel}:struts-config"));
            } else if framework_build_files().contains(lower.as_str()) {
                let content = read_limited_lower(&path, usize::MAX);
                if content.contains("struts2") || content.contains("org.apache.struts") {
                    evidence.push(format!("{rel}:struts-dependency"));
                }
            }
        }
    }
    evidence.sort();
    evidence
}

//! Port `tools/struts/pipeline.py` — run_struts_analysis.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use super::java_validation::parse_java_validation_hooks;
use super::models::{Diagnostic, PackageConfig, Params, StrutsAnalysisResult, ValidationRule, WebFilterConfig};
use super::resolver::{resolve_struts_project, ResolveInput};
use super::struts_xml_parser::parse_struts_xml_file;
use super::validation_parser::parse_validation_xml_file;
use super::web_xml_parser::parse_web_xml_file;

const IGNORED_DIR_PATTERNS: [&str; 38] = [
    ".git",
    ".hg",
    ".svn",
    ".eclipse",
    ".idea",
    ".settings",
    ".vs",
    ".vscode",
    "bin",
    "build",
    "buildSrc",
    "classes",
    "generated",
    "generated-sources",
    "generated-test-sources",
    "out",
    "target",
    ".gradle",
    ".mvn",
    "dist",
    "node_modules",
    ".cache",
    ".parcel-cache",
    "__pycache__",
    "coverage",
    "failsafe-reports",
    "junit",
    "surefire-reports",
    "test-results",
    ".tmp",
    ".venv",
    "env",
    "temp",
    "tmp",
    "tmpdir",
    "venv",
    "virtualenv",
    "*.egg-info",
];

const IGNORED_FILE_PATTERNS: [&str; 17] = [
    "*.class",
    "*.ear",
    "*.jar",
    "*.war",
    "*.iml",
    "*.ipr",
    "*.iws",
    "*.swo",
    "*.swp",
    "*.bak",
    "*.log",
    "*.orig",
    "*.rej",
    "*.tmp",
    "*~",
    ".DS_Store",
    "Thumbs.db",
];

/// fnmatch casefold cả hai phía (`fnmatchcase(normalized, pattern)`).
fn matches_pattern(name: &str, patterns: &[&str]) -> bool {
    let normalized = name.to_lowercase();
    patterns.iter().any(|pattern| fnmatch(&normalized, &pattern.to_lowercase()))
}

/// fnmatch subset: `*`, `?`, `[]` char class, `.` literal.
fn fnmatch(text: &str, pattern: &str) -> bool {
    fn inner(text: &[u8], pattern: &[u8]) -> bool {
        let (mut t, mut p) = (0usize, 0usize);
        let (mut star, mut star_t) = (None::<usize>, 0usize);
        while t < text.len() {
            if p < pattern.len() && (pattern[p] == b'?' || pattern[p] == text[t]) {
                t += 1;
                p += 1;
            } else if p < pattern.len() && pattern[p] == b'*' {
                star = Some(p);
                star_t = t;
                p += 1;
            } else if let Some(sp) = star {
                p = sp + 1;
                star_t += 1;
                t = star_t;
            } else {
                return false;
            }
        }
        while p < pattern.len() && pattern[p] == b'*' {
            p += 1;
        }
        p == pattern.len()
    }
    inner(text.as_bytes(), pattern.as_bytes())
}

fn iter_files(root: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();
    walk(root, &mut out);
    out
}

fn walk(current: &Path, out: &mut Vec<PathBuf>) {
    let entries = match std::fs::read_dir(current) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut dirs: Vec<PathBuf> = Vec::new();
    let mut files: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            dirs.push(path);
        } else {
            files.push(path);
        }
    }
    dirs.sort();
    files.sort();
    dirs.retain(|dir| {
        let name = dir.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
        !name.starts_with('.') && !matches_pattern(&name, &IGNORED_DIR_PATTERNS)
    });
    for file in files.drain(..) {
        let name = file.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
        if matches_pattern(&name, &IGNORED_FILE_PATTERNS) {
            continue;
        }
        out.push(file);
    }
    for dir in dirs {
        walk(&dir, out);
    }
}

fn path_selected(relative: &str, selected_paths: &BTreeSet<String>) -> bool {
    if selected_paths.is_empty() {
        return true;
    }
    selected_paths.iter().any(|item| {
        let prefix = item.trim_end_matches('/');
        relative == item || relative.starts_with(&format!("{prefix}/"))
    })
}

fn resolve_include(project_root: &Path, source_file: &str, include: &str) -> Option<String> {
    let candidates = [
        project_root.join(include),
        project_root.join(Path::new(source_file).parent().unwrap_or(Path::new(""))).join(include),
    ];
    for candidate in candidates {
        let resolved = crate::pyutil::realpath(&candidate);
        let Ok(relative) = resolved.strip_prefix(project_root) else {
            continue;
        };
        if resolved.is_file() {
            return Some(crate::pyutil::to_posix(relative));
        }
    }
    None
}

fn load_struts_configs(
    project_root: &Path,
    initial_files: &[String],
) -> (Vec<PackageConfig>, Params, Vec<Diagnostic>) {
    let mut packages: Vec<PackageConfig> = Vec::new();
    let mut constants: Params = Params::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let mut queue: Vec<String> = initial_files.to_vec();
    queue.sort();
    let mut visited: BTreeSet<String> = BTreeSet::new();
    while !queue.is_empty() {
        let file_path = queue.remove(0);
        if visited.contains(&file_path) {
            continue;
        }
        visited.insert(file_path.clone());
        let parsed = parse_struts_xml_file(&project_root.to_string_lossy(), &file_path);
        packages.extend(parsed.packages);
        for (key, value) in parsed.constants {
            constants.insert(key, value);
        }
        diagnostics.extend(parsed.diagnostics);
        for include in parsed.includes {
            let resolved = resolve_include(project_root, &file_path, &include);
            match resolved {
                None => diagnostics.push(Diagnostic::new(
                    "struts.config.include_missing",
                    &format!("Unable to resolve included Struts configuration {include:?}"),
                    "warning",
                    &file_path,
                )),
                Some(resolved) if !visited.contains(&resolved) => queue.push(resolved),
                Some(_) => {}
            }
        }
    }
    (packages, constants, diagnostics)
}

pub fn run_struts_analysis(
    root: &str,
    project_id: &str,
    project_name: &str,
    selected_paths: &[String],
) -> StrutsAnalysisResult {
    let project_root = crate::pyutil::realpath(Path::new(root));
    let display_name = if project_name.is_empty() { project_id } else { project_name };
    let selected: BTreeSet<String> = selected_paths
        .iter()
        .filter(|item| !item.is_empty())
        .map(|item| item.replace('\\', "/").trim_matches('/').to_string())
        .collect();

    let mut web_xml_files: Vec<String> = Vec::new();
    let mut struts_xml_files: Vec<String> = Vec::new();
    let mut validation_files: Vec<String> = Vec::new();
    let mut java_files: Vec<String> = Vec::new();
    let mut convention_detected = false;

    if !project_root.is_dir() {
        return StrutsAnalysisResult {
            project_id: project_id.to_string(),
            project_name: display_name.to_string(),
            root: project_root.to_string_lossy().to_string(),
            diagnostics: vec![Diagnostic::new(
                "struts.root.invalid",
                "Project root is not a directory",
                "error",
                &project_root.to_string_lossy(),
            )],
            coverage_status: "empty".to_string(),
            parser_version: super::models::STRUTS_PARSER_VERSION.to_string(),
            ..Default::default()
        };
    }

    for path in iter_files(&project_root) {
        let Ok(relative_path) = path.strip_prefix(&project_root) else {
            continue;
        };
        let relative = crate::pyutil::to_posix(relative_path);
        if !path_selected(&relative, &selected) {
            continue;
        }
        let lower_name = path
            .file_name()
            .map(|n| n.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        if lower_name == "web.xml" {
            web_xml_files.push(relative);
        } else if lower_name.ends_with("-validation.xml") {
            validation_files.push(relative);
        } else if lower_name == "struts.xml" || lower_name == "struts-plugin.xml" || (lower_name.starts_with("struts-") && lower_name.ends_with(".xml")) {
            struts_xml_files.push(relative);
        } else if lower_name.ends_with(".java") {
            java_files.push(relative);
        } else if (lower_name == "pom.xml" || lower_name == "build.gradle" || lower_name == "build.gradle.kts")
            && let Ok(text) = std::fs::read_to_string(&path)
        {
            convention_detected = convention_detected || text.contains("struts2-convention-plugin");
        }
    }

    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let (packages, constants, mut config_diagnostics) =
        load_struts_configs(&project_root, &struts_xml_files);
    diagnostics.append(&mut config_diagnostics);
    let mut web_filters: Vec<WebFilterConfig> = Vec::new();
    web_xml_files.sort();
    for file_path in &web_xml_files {
        let parsed = parse_web_xml_file(&project_root.to_string_lossy(), file_path);
        web_filters.extend(parsed.filters);
        diagnostics.extend(parsed.diagnostics);
    }
    let mut validation_rules: Vec<ValidationRule> = Vec::new();
    validation_files.sort();
    for file_path in &validation_files {
        let parsed = parse_validation_xml_file(&project_root.to_string_lossy(), file_path);
        validation_rules.extend(parsed.rules);
        diagnostics.extend(parsed.diagnostics);
    }
    java_files.sort();
    for file_path in &java_files {
        let parsed = parse_java_validation_hooks(&project_root.to_string_lossy(), file_path);
        validation_rules.extend(parsed.rules);
        diagnostics.extend(parsed.diagnostics);
    }

    if convention_detected {
        diagnostics.push(Diagnostic::new(
            "struts.convention.partial",
            "Convention Plugin detected; annotation and classpath-derived routes are outside the XML-first MVP",
            "warning",
            "",
        ));
    }

    let resolution = resolve_struts_project(&ResolveInput {
        project_id,
        project_name: display_name,
        module_id: project_id,
        packages: &packages,
        constants: &constants,
        web_filters: &web_filters,
        validation_rules: &validation_rules,
    });
    diagnostics.extend(resolution.diagnostics);
    let has_inputs = !struts_xml_files.is_empty() || !web_xml_files.is_empty() || !validation_files.is_empty();
    let has_errors = diagnostics.iter().any(|item| item.severity == "error");
    let has_partial = diagnostics.iter().any(|item| item.severity == "warning");
    let coverage = if !has_inputs {
        "empty"
    } else if has_errors || has_partial || packages.is_empty() {
        "partial"
    } else {
        "complete"
    };

    // unique_diagnostics keyed (code, message, severity, file_path) — giữ
    // item cuối, sau đó sort theo (file_path, code, message).
    let mut unique: BTreeSet<(String, String, String, String)> = BTreeSet::new();
    let mut unique_items: Vec<Diagnostic> = Vec::new();
    for item in &diagnostics {
        let key = (item.code.clone(), item.message.clone(), item.severity.clone(), item.file_path.clone());
        if unique.contains(&key) {
            continue;
        }
        unique.insert(key);
        unique_items.push(item.clone());
    }
    unique_items.sort_by(|a, b| (&a.file_path, &a.code, &a.message).cmp(&(&b.file_path, &b.code, &b.message)));

    StrutsAnalysisResult {
        project_id: project_id.to_string(),
        project_name: display_name.to_string(),
        root: project_root.to_string_lossy().to_string(),
        semantic_facts: resolution.facts,
        relationships: resolution.relationships,
        diagnostics: unique_items,
        coverage_status: coverage.to_string(),
        parser_version: super::models::STRUTS_PARSER_VERSION.to_string(),
    }
}

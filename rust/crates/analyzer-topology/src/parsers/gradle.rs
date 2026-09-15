//! Port `parsers/gradle.py` — conservative Gradle Groovy/Kotlin DSL extraction.
//! Regex giữ nguyên chữ với Python (LazyLock phía cuối file).

use std::sync::LazyLock;

use regex::Regex;

use crate::models::{
    confidence, dependency_scope, descriptor_role, descriptor_type, module_kind,
    normalize_module_path, parse_depth, safe_summary, sorted_unique, DependencyFact, DescriptorFact,
    PyValue, SourceEvidence,
};
use crate::parsers::common::{dynamic_diagnostics, evidence, line_number, module_path_for_file};
use crate::parsers::DescriptorParseOutput;

static ROOT_NAME_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"\brootProject\.name\s*=\s*['\"]([^'\"]+)['\"]"#).expect("regex")
});
static INCLUDE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\binclude\s*(?:\(|\s)\s*([^\n)]+)\)?").expect("regex"));
static QUOTED_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"['\"]([^'\"]+)['\"]"#).expect("regex"));
static PROJECT_DIR_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"\bproject\s*\(\s*['\"](:[^'\"]+)['\"]\s*\)\.projectDir\s*=\s*(?:file\s*\(\s*)?['\"]([^'\"]+)['\"]"#,
    )
    .expect("regex")
});
static INCLUDED_BUILD_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"\bincludeBuild\s*\(\s*['\"]([^'\"]+)['\"]"#).expect("regex"));
static PLUGIN_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?:id\s*\(?\s*['\"]([^'\"]+)['\"]|alias\s*\(\s*libs\.plugins\.([A-Za-z0-9_.-]+))"#)
        .expect("regex")
});
static NAMESPACE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"\b(namespace|applicationId)\s*(?:=|\s)\s*['\"]([^'\"]+)['\"]"#).expect("regex")
});
static PROJECT_DEP_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"\b([A-Za-z][A-Za-z0-9_]*)\s*\(?\s*(?:project\s*\(\s*(?:path\s*:\s*)?['\"](:[^'\"]+)['\"]\s*\)|projects\.([A-Za-z0-9_.]+))"#,
    )
    .expect("regex")
});
static EXTERNAL_DEP_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"\b([A-Za-z][A-Za-z0-9_]*)\s*\(?\s*['\"]([A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+(?::[^'\"]+)?)['\"]"#,
    )
    .expect("regex")
});
static DYNAMIC_FEATURE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\bdynamicFeatures\s*(?:=|\+=)\s*setOf\s*\(([^)]*)\)").expect("regex"));
static SOURCE_SET_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\bsourceSets\s*(?:\{|\.)([A-Za-z0-9_-]+)").expect("regex"));

/// Capture như Python finditer: offset match.start() + groups (1-based).
struct Caps {
    start: usize,
    groups: Vec<Option<String>>,
}

fn captures(re: &Regex, text: &str) -> Vec<Caps> {
    re.captures_iter(text)
        .map(|caps| Caps {
            start: caps.get(0).map(|m| m.start()).unwrap_or(0),
            groups: (1..caps.len())
                .map(|index| caps.get(index).map(|m| m.as_str().to_string()))
                .collect(),
        })
        .collect()
}

impl Caps {
    fn group(&self, index: usize) -> Option<String> {
        self.groups.get(index - 1).cloned().flatten()
    }
}

fn quoted_all(text: &str) -> Vec<String> {
    captures(&QUOTED_RE, text)
        .iter()
        .filter_map(|caps| caps.group(1))
        .collect()
}

fn _gradle_path(value: &str) -> String {
    let stripped = value.trim().trim_matches(':');
    normalize_module_path(&stripped.replace(':', "/")).unwrap_or_else(|_| ".".to_string())
}

fn _scope(configuration: &str) -> &'static str {
    let lowered = configuration.to_lowercase();
    if lowered.contains("test") {
        return dependency_scope::TEST;
    }
    if lowered.contains("runtime") {
        return dependency_scope::RUNTIME;
    }
    if lowered.contains("compileonly") || lowered.contains("provided") {
        return dependency_scope::PROVIDED;
    }
    if lowered.contains("plugin") || lowered.contains("classpath") {
        return dependency_scope::PLUGIN;
    }
    dependency_scope::COMPILE
}

/// `str(PurePosixPath(module_path) / target)` — pathlib join: absolute reset,
/// '.' segments dropped, slashes collapsed.
fn pjoin(module_path: &str, target: &str) -> String {
    if target.starts_with('/') {
        return pure_posix_str(target);
    }
    let mut joined = module_path.to_string();
    for piece in target.split('/') {
        if piece.is_empty() || piece == "." {
            continue;
        }
        if joined == "." {
            joined = piece.to_string();
        } else {
            joined = format!("{joined}/{piece}");
        }
    }
    pure_posix_str(&joined)
}

fn pure_posix_str(path: &str) -> String {
    if path.is_empty() {
        return ".".to_string();
    }
    let mut leading = String::new();
    let mut rest = path;
    if rest.starts_with("//") && !rest.starts_with("///") {
        leading.push_str("//");
        rest = &rest[2..];
    } else if rest.starts_with('/') {
        leading.push('/');
        rest = &rest[1..];
    }
    let pieces: Vec<&str> = rest
        .split('/')
        .filter(|piece| !piece.is_empty() && *piece != ".")
        .collect();
    let joined = pieces.join("/");
    if joined.is_empty() {
        if leading.is_empty() {
            ".".to_string()
        } else {
            leading
        }
    } else {
        format!("{leading}{joined}")
    }
}

fn resolve_under(module_path: &str, value: &str) -> String {
    let joined = if module_path != "." {
        pjoin(module_path, value)
    } else {
        value.to_string()
    };
    normalize_module_path(&joined).unwrap_or_else(|_| ".".to_string())
}

pub fn parse_gradle_settings(project_id: &str, path: &str, text: &str) -> DescriptorParseOutput {
    let module_path = module_path_for_file(path);
    let diagnostics = dynamic_diagnostics(text, path, &module_path);
    let root_match = captures(&ROOT_NAME_RE, text)
        .first()
        .and_then(|caps| caps.group(1));
    let mut declared: Vec<String> = Vec::new();
    for caps in captures(&INCLUDE_RE, text) {
        let Some(target) = caps.group(1) else { continue };
        for value in quoted_all(&target) {
            if !value.trim_matches(':').is_empty() {
                declared.push(_gradle_path(&value));
            }
        }
    }
    // Python dict comprehension: key trùng ghi đè value, thứ tự = match order.
    let mut project_dirs_order: Vec<(String, String)> = Vec::new();
    for caps in captures(&PROJECT_DIR_RE, text) {
        let Some(name) = caps.group(1) else { continue };
        let Some(target) = caps.group(2) else { continue };
        let key = _gradle_path(&name);
        let value = resolve_under(&module_path, &target);
        if let Some(entry) = project_dirs_order.iter_mut().find(|(k, _)| *k == key) {
            entry.1 = value;
        } else {
            project_dirs_order.push((key, value));
        }
    }
    let declared: Vec<String> = declared
        .into_iter()
        .map(|item| {
            project_dirs_order
                .iter()
                .find(|(key, _)| *key == item)
                .map(|(_, value)| value.clone())
                .unwrap_or(item)
        })
        .collect();
    let included_builds: Vec<String> = captures(&INCLUDED_BUILD_RE, text)
        .iter()
        .filter_map(|caps| caps.group(1))
        .map(|value| resolve_under(&module_path, &value))
        .collect();

    let mut properties = PyValue::dict();
    properties.set("root_name", PyValue::Str(root_match.clone().unwrap_or_default()));
    properties.set(
        "declared_modules",
        PyValue::List(sorted_unique(declared).into_iter().map(PyValue::Str).collect()),
    );
    let mut project_dirs_value = PyValue::dict();
    for (key, value) in &project_dirs_order {
        project_dirs_value.set(key, PyValue::Str(value.clone()));
    }
    properties.set("project_dirs", project_dirs_value);
    properties.set(
        "included_builds",
        PyValue::List(
            sorted_unique(included_builds)
                .into_iter()
                .map(PyValue::Str)
                .collect(),
        ),
    );
    properties.set("build_system", PyValue::Str("gradle".to_string()));
    let summary = safe_summary(&format!(
        "Gradle settings for {}",
        root_match.unwrap_or_else(|| module_path.clone())
    ));
    let descriptor = DescriptorFact::create(
        project_id,
        &module_path,
        path,
        descriptor_type::GRADLE_SETTINGS,
        descriptor_role::TOPOLOGY,
        "gradle",
        parse_depth::TOPOLOGY,
        summary,
        properties,
        if diagnostics.is_empty() { confidence::HIGH } else { confidence::MEDIUM },
        evidence(path),
        diagnostics.clone(),
    )
    .expect("gradle settings descriptor");
    DescriptorParseOutput {
        descriptor,
        dependencies: vec![],
        endpoints: vec![],
        diagnostics,
    }
}

fn _module_kind(plugins: &[String]) -> &'static str {
    let lowered: Vec<String> = plugins.iter().map(|item| item.to_lowercase()).collect();
    let has_any = |needles: &[&str]| {
        lowered
            .iter()
            .any(|item| needles.iter().any(|needle| item == needle))
    };
    if has_any(&["com.android.dynamic-feature", "android-dynamic-feature"]) {
        return module_kind::ANDROID_DYNAMIC_FEATURE;
    }
    if has_any(&["com.android.application", "android-application"]) {
        return module_kind::ANDROID_APPLICATION;
    }
    if has_any(&["com.android.library", "android-library"]) {
        return module_kind::ANDROID_LIBRARY;
    }
    if lowered
        .iter()
        .any(|item| item == "application" || item.ends_with(".application"))
    {
        return module_kind::JVM_APPLICATION;
    }
    if lowered.iter().any(|item| {
        item.contains("java-library")
            || item.contains("kotlin.jvm")
            || item.contains("org.jetbrains.kotlin")
    }) {
        return module_kind::JVM_LIBRARY;
    }
    module_kind::UNKNOWN
}

pub fn parse_gradle_build(project_id: &str, path: &str, text: &str) -> DescriptorParseOutput {
    let module_path = module_path_for_file(path);
    let diagnostics = dynamic_diagnostics(text, path, &module_path);
    let plugins: Vec<String> = captures(&PLUGIN_RE, text)
        .iter()
        .filter_map(|caps| match caps.group(1) {
            Some(first) if !first.is_empty() => Some(first),
            _ => caps.group(2),
        })
        .collect();
    let mut coordinates: Vec<(String, String)> = Vec::new();
    for caps in captures(&NAMESPACE_RE, text) {
        if let (Some(key), Some(value)) = (caps.group(1), caps.group(2)) {
            if let Some(entry) = coordinates.iter_mut().find(|(k, _)| *k == key) {
                entry.1 = value;
            } else {
                coordinates.push((key, value));
            }
        }
    }
    let coordinate_value = |key: &str| {
        coordinates
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, value)| value.clone())
            .unwrap_or_default()
    };
    let mut dependencies: Vec<DependencyFact> = Vec::new();
    for caps in captures(&PROJECT_DEP_RE, text) {
        let Some(configuration) = caps.group(1) else { continue };
        let colon_path = caps.group(2);
        let accessor = caps.group(3).unwrap_or_default();
        let raw_target = colon_path.unwrap_or_else(|| accessor.replace('.', ":"));
        let target_path = _gradle_path(&raw_target);
        if let Ok(dependency) = DependencyFact::create(
            project_id,
            &module_path,
            &target_path,
            _scope(&configuration),
            Some(target_path.clone()),
            true,
            path,
            vec![SourceEvidence::at_line(path, line_number(text, caps.start))],
            PyValue::dict(),
        ) {
            dependencies.push(dependency);
        }
    }
    for caps in captures(&EXTERNAL_DEP_RE, text) {
        let Some(configuration) = caps.group(1) else { continue };
        let Some(coordinate) = caps.group(2) else { continue };
        if let Ok(dependency) = DependencyFact::create(
            project_id,
            &module_path,
            &coordinate,
            _scope(&configuration),
            None,
            false,
            path,
            vec![SourceEvidence::at_line(path, line_number(text, caps.start))],
            PyValue::dict(),
        ) {
            dependencies.push(dependency);
        }
    }
    let mut dynamic_features: Vec<String> = Vec::new();
    for caps in captures(&DYNAMIC_FEATURE_RE, text) {
        let Some(block) = caps.group(1) else { continue };
        for value in quoted_all(&block) {
            dynamic_features.push(_gradle_path(&value));
        }
    }
    for target in &dynamic_features {
        let mut properties = PyValue::dict();
        properties.set("dynamic_feature", PyValue::Bool(true));
        if let Ok(dependency) = DependencyFact::create(
            project_id,
            &module_path,
            target,
            dependency_scope::COMPILE,
            Some(target.clone()),
            true,
            path,
            evidence(path),
            properties,
        ) {
            dependencies.push(dependency);
        }
    }
    dependencies.sort_by(|a, b| a.id.cmp(&b.id));
    let module_kind_value = _module_kind(&plugins);
    let mut properties = PyValue::dict();
    properties.set(
        "plugins",
        PyValue::List(
            sorted_unique(plugins.clone())
                .into_iter()
                .map(PyValue::Str)
                .collect(),
        ),
    );
    properties.set("module_kind", PyValue::Str(module_kind_value.to_string()));
    properties.set("namespace", PyValue::Str(coordinate_value("namespace")));
    properties.set(
        "application_id",
        PyValue::Str(coordinate_value("applicationId")),
    );
    properties.set(
        "source_sets",
        PyValue::List(
            sorted_unique(
                captures(&SOURCE_SET_RE, text)
                    .iter()
                    .filter_map(|caps| caps.group(1)),
            )
            .into_iter()
            .map(PyValue::Str)
            .collect(),
        ),
    );
    properties.set(
        "dynamic_features",
        PyValue::List(
            sorted_unique(dynamic_features)
                .into_iter()
                .map(PyValue::Str)
                .collect(),
        ),
    );
    properties.set("build_system", PyValue::Str("gradle".to_string()));
    let summary = safe_summary(&format!(
        "Gradle {} module at {}",
        module_kind_value, module_path
    ));
    let descriptor = DescriptorFact::create(
        project_id,
        &module_path,
        path,
        descriptor_type::GRADLE_BUILD,
        descriptor_role::DEPENDENCY,
        "gradle",
        parse_depth::DEPENDENCY,
        summary,
        properties,
        if diagnostics.is_empty() { confidence::HIGH } else { confidence::MEDIUM },
        evidence(path),
        diagnostics.clone(),
    )
    .expect("gradle build descriptor");
    DescriptorParseOutput {
        descriptor,
        dependencies,
        endpoints: vec![],
        diagnostics,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn settings_parses_includes_and_root_name() {
        let output = parse_gradle_settings(
            "Bank",
            "settings.gradle",
            "rootProject.name = 'Bank'\ninclude ':app', ':lib:core'\n",
        );
        assert_eq!(output.descriptor.properties.get("root_name").unwrap().as_str(), "Bank");
        let declared = output
            .descriptor
            .properties
            .get("declared_modules")
            .unwrap();
        match declared {
            PyValue::List(items) => {
                let names: Vec<&str> = items.iter().map(PyValue::as_str).collect();
                assert_eq!(names, ["app", "lib/core"]);
            }
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn build_detects_android_app_and_project_dep() {
        let output = parse_gradle_build(
            "Bank",
            "app/build.gradle",
            "plugins { id 'com.android.application' }\nandroid { namespace 'com.bank' }\ndependencies {\n  implementation project(':lib')\n  testImplementation 'junit:junit:4.13.2'\n}\n",
        );
        assert_eq!(
            output.descriptor.properties.get("module_kind").unwrap().as_str(),
            "android_application"
        );
        assert_eq!(output.dependencies.len(), 2);
        assert!(output.dependencies.iter().any(|d| d.internal));
        assert!(output.dependencies.iter().any(|d| !d.internal && d.scope == "test"));
    }
}

//! Port `parsers/make.py` — conservative Make target/prerequisite extraction.
//! `_TARGET_RE` dùng `(?![=])` lookahead → fancy-regex.

use std::sync::LazyLock;

use fancy_regex::Regex as FancyRegex;
use regex::Regex;

use crate::models::{
    confidence, dependency_scope, descriptor_role, descriptor_type, parse_depth, safe_summary,
    sorted_unique, AnalysisDiagnostic, DependencyFact, DescriptorFact, PyValue, SourceEvidence,
};
use crate::parsers::common::{evidence, line_number, module_path_for_file};
use crate::parsers::DescriptorParseOutput;

static TARGET_RE: LazyLock<FancyRegex> = LazyLock::new(|| {
    FancyRegex::new(r"(?m)^([A-Za-z0-9_./%+-][^:=\n]*?)\s*:(?![=])\s*([^\n#]*)").expect("regex")
});
static INCLUDE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?m)^\s*-?include\s+([^\n#]+)").expect("regex"));
static VARIABLE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?m)^([A-Za-z_][A-Za-z0-9_]*)\s*[:?+]?=\s*([^\n#]*)").expect("regex"));
static DYNAMIC_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\$\((?:shell|eval|call)\b|`[^`]+`").expect("regex"));

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

fn fancy_captures(re: &FancyRegex, text: &str) -> Vec<Caps> {
    re.captures_iter(text)
        .flatten()
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

pub fn parse_make(project_id: &str, path: &str, text: &str) -> DescriptorParseOutput {
    let module_path = module_path_for_file(path);
    let mut diagnostics: Vec<AnalysisDiagnostic> = Vec::new();
    for caps in captures(&DYNAMIC_RE, text) {
        let mut details = PyValue::dict();
        details.set("line", PyValue::Int(line_number(text, caps.start)));
        diagnostics.push(
            AnalysisDiagnostic::new(
                crate::models::diagnostic_code::UNSUPPORTED_CONSTRUCT,
                "Dynamic Make expression was not evaluated.",
            )
            .file_path(path)
            .module_path(&module_path)
            .details(details),
        );
    }
    let mut targets: Vec<PyValue> = Vec::new();
    let mut dependencies: Vec<DependencyFact> = Vec::new();
    for caps in fancy_captures(&TARGET_RE, text) {
        let Some(names_text) = caps.group(1) else { continue };
        let Some(prereq_text) = caps.group(2) else { continue };
        let names: Vec<&str> = names_text
            .split_whitespace()
            .filter(|item| !item.contains('$'))
            .collect();
        let prerequisites: Vec<&str> = prereq_text
            .split_whitespace()
            .filter(|item| !item.contains('$'))
            .collect();
        for name in names {
            let mut entry = PyValue::dict();
            entry.set("name", PyValue::Str(name.to_string()));
            entry.set(
                "prerequisites",
                PyValue::List(prerequisites.iter().map(|p| PyValue::Str(p.to_string())).collect()),
            );
            targets.push(entry);
            for target in &prerequisites {
                let mut properties = PyValue::dict();
                properties.set("make_target", PyValue::Str(name.to_string()));
                if let Ok(dependency) = DependencyFact::create(
                    project_id,
                    &module_path,
                    target,
                    dependency_scope::BUILD,
                    None,
                    false,
                    path,
                    vec![SourceEvidence::at_line(path, line_number(text, caps.start))],
                    properties,
                ) {
                    dependencies.push(dependency);
                }
            }
        }
    }
    let mut variables: Vec<(String, String)> = Vec::new();
    for caps in captures(&VARIABLE_RE, text) {
        let Some(name) = caps.group(1) else { continue };
        let Some(value) = caps.group(2) else { continue };
        if DYNAMIC_RE.is_match(&value) {
            continue;
        }
        let value = value.trim().to_string();
        if let Some(entry) = variables.iter_mut().find(|(key, _)| *key == name) {
            entry.1 = value;
        } else {
            variables.push((name, value));
        }
    }
    let mut includes: Vec<String> = Vec::new();
    for caps in captures(&INCLUDE_RE, text) {
        let Some(value) = caps.group(1) else { continue };
        for item in value.split_whitespace() {
            if !item.contains('$') {
                includes.push(item.to_string());
            }
        }
    }
    let mut properties = PyValue::dict();
    properties.set("targets", PyValue::List(targets.clone()));
    properties.set(
        "includes",
        PyValue::List(
            sorted_unique(includes)
                .into_iter()
                .map(PyValue::Str)
                .collect(),
        ),
    );
    let mut variables_value = PyValue::dict();
    for (key, value) in &variables {
        variables_value.set(key, PyValue::Str(value.clone()));
    }
    properties.set("variables", variables_value);
    properties.set("build_system", PyValue::Str("make".to_string()));
    let summary = safe_summary(&format!(
        "Make build with {} literal targets",
        targets.len()
    ));
    let descriptor = DescriptorFact::create(
        project_id,
        &module_path,
        path,
        descriptor_type::MAKE,
        descriptor_role::TOPOLOGY,
        "make",
        parse_depth::DEPENDENCY,
        summary,
        properties,
        if diagnostics.is_empty() { confidence::HIGH } else { confidence::MEDIUM },
        evidence(path),
        diagnostics.clone(),
    )
    .expect("make descriptor");
    DescriptorParseOutput {
        descriptor,
        dependencies,
        endpoints: vec![],
        diagnostics,
    }
}

//! Port `parsers/cmake.py` — bounded CMake statement extraction.

use std::sync::LazyLock;

use fancy_regex::Regex as FancyRegex;
use regex::Regex;

use crate::models::{
    confidence, dependency_scope, descriptor_role, descriptor_type, module_kind,
    normalize_module_path, parse_depth, safe_summary, sorted_unique, AnalysisDiagnostic,
    DependencyFact, DescriptorFact, PyValue, SourceEvidence,
};
use crate::parsers::common::{dynamic_diagnostics, evidence, line_number, module_path_for_file};
use crate::parsers::DescriptorParseOutput;

static COMMAND_RE: LazyLock<FancyRegex> =
    LazyLock::new(|| FancyRegex::new(r"(?isim)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)").expect("regex"));
static TOKEN_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#""([^"]*)"|'([^']*)'|([^\s;]+)"#).expect("regex"));

struct CommandMatch {
    start: usize,
    command: String,
    args_text: String,
}

fn command_matches(text: &str) -> Vec<CommandMatch> {
    let mut out = Vec::new();
    let re = &*COMMAND_RE;
    for caps in re.captures_iter(text).flatten() {
        let whole = caps.get(0).expect("group 0");
        let command = caps
            .get(1)
            .map(|m| m.as_str().to_string())
            .unwrap_or_default();
        let args_text = caps
            .get(2)
            .map(|m| m.as_str().to_string())
            .unwrap_or_default();
        out.push(CommandMatch {
            start: whole.start(),
            command,
            args_text,
        });
    }
    out
}

fn tokens(value: &str) -> Vec<String> {
    TOKEN_RE
        .captures_iter(value)
        .filter_map(|caps| {
            caps.get(1)
                .or_else(|| caps.get(2))
                .or_else(|| caps.get(3))
                .map(|m| m.as_str().to_string())
        })
        .collect()
}

pub fn parse_cmake(project_id: &str, path: &str, text: &str) -> DescriptorParseOutput {
    let module_path = module_path_for_file(path);
    let mut diagnostics = dynamic_diagnostics(text, path, &module_path);
    let mut project_name = String::new();
    let mut subdirectories: Vec<String> = Vec::new();
    let mut targets: Vec<PyValue> = Vec::new();
    let mut dependencies: Vec<DependencyFact> = Vec::new();
    let mut target_kinds: Vec<(String, &'static str)> = Vec::new();
    for matched in command_matches(text) {
        let command = matched.command.to_lowercase();
        let args = tokens(&matched.args_text);
        if args.is_empty() {
            continue;
        }
        if args.iter().any(|value| value.contains("$<")) {
            diagnostics.push(
                AnalysisDiagnostic::new(
                    crate::models::diagnostic_code::DYNAMIC_EXPRESSION,
                    "CMake generator expression was retained as unresolved evidence.",
                )
                .file_path(path)
                .module_path(&module_path)
                .details({
                    let mut details = PyValue::dict();
                    details.set("line", PyValue::Int(line_number(text, matched.start)));
                    details
                }),
            );
        }
        if command == "project" {
            project_name = args[0].clone();
        } else if command == "add_subdirectory" {
            let joined = if module_path != "." {
                format!("{module_path}/{}", args[0])
            } else {
                args[0].clone()
            };
            subdirectories.push(normalize_module_path(&joined).unwrap_or_else(|_| ".".to_string()));
        } else if command == "add_executable" || command == "add_library" {
            let kind = if command == "add_executable" {
                module_kind::NATIVE_EXECUTABLE
            } else {
                module_kind::NATIVE_LIBRARY
            };
            if let Some(entry) = target_kinds.iter_mut().find(|(name, _)| *name == args[0]) {
                entry.1 = kind;
            } else {
                target_kinds.push((args[0].clone(), kind));
            }
            let mut entry = PyValue::dict();
            entry.set("name", PyValue::Str(args[0].clone()));
            entry.set("kind", PyValue::Str(kind.to_string()));
            entry.set(
                "sources",
                PyValue::List(args[1..].iter().cloned().map(PyValue::Str).collect()),
            );
            targets.push(entry);
        } else if (command == "target_link_libraries" || command == "add_dependencies")
            && args.len() > 1
        {
            let source_target = args[0].clone();
            for target in &args[1..] {
                if ["PUBLIC", "PRIVATE", "INTERFACE"].contains(&target.to_uppercase().as_str()) {
                    continue;
                }
                let mut properties = PyValue::dict();
                properties.set("source_target", PyValue::Str(source_target.clone()));
                if let Ok(dependency) = DependencyFact::create(
                    project_id,
                    &module_path,
                    target,
                    dependency_scope::COMPILE,
                    None,
                    false,
                    path,
                    vec![SourceEvidence::at_line(path, line_number(text, matched.start))],
                    properties,
                ) {
                    dependencies.push(dependency);
                }
            }
        }
    }
    let unique_kinds: Vec<&'static str> = {
        let mut seen: Vec<&'static str> = Vec::new();
        for (_, kind) in &target_kinds {
            if !seen.contains(kind) {
                seen.push(kind);
            }
        }
        seen
    };
    let module_kind_value = if unique_kinds.len() == 1 {
        unique_kinds[0]
    } else {
        module_kind::UNKNOWN
    };
    dependencies.sort_by(|a, b| a.id.cmp(&b.id));
    let mut properties = PyValue::dict();
    properties.set("project_name", PyValue::Str(project_name.clone()));
    properties.set(
        "declared_modules",
        PyValue::List(
            sorted_unique(subdirectories)
                .into_iter()
                .map(PyValue::Str)
                .collect(),
        ),
    );
    properties.set("targets", PyValue::List(targets));
    properties.set("module_kind", PyValue::Str(module_kind_value.to_string()));
    properties.set("build_system", PyValue::Str("cmake".to_string()));
    let summary = safe_summary(&format!(
        "CMake project {}",
        if project_name.is_empty() {
            module_path.clone()
        } else {
            project_name
        }
    ));
    let descriptor = DescriptorFact::create(
        project_id,
        &module_path,
        path,
        descriptor_type::CMAKE,
        descriptor_role::TOPOLOGY,
        "cmake",
        parse_depth::DEPENDENCY,
        summary,
        properties,
        if diagnostics.is_empty() { confidence::HIGH } else { confidence::MEDIUM },
        evidence(path),
        diagnostics.clone(),
    )
    .expect("cmake descriptor");
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
    fn parses_project_targets_and_links() {
        let output = parse_cmake(
            "Bank",
            "CMakeLists.txt",
            "project(bankcore)\nadd_library(bankcore src/a.cpp)\ntarget_link_libraries(bankcore PUBLIC sqlite3)\nadd_subdirectory(plugins)\n",
        );
        assert_eq!(
            output.descriptor.properties.get("project_name").unwrap().as_str(),
            "bankcore"
        );
        assert_eq!(output.dependencies.len(), 1);
        assert_eq!(output.dependencies[0].target, "sqlite3");
    }
}

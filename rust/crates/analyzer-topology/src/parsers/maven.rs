//! Port `parsers/maven.py` — namespace-safe bounded Maven POM extraction.
//! Error message của ET.ParseError được tái tạo expat-compatible qua `etdom`.

use std::sync::LazyLock;

use regex::Regex;

use crate::etdom::EtNode;
use crate::models::{
    confidence, dependency_scope, descriptor_role, descriptor_type, module_kind,
    normalize_module_path, parse_depth, safe_summary, sorted_unique, AnalysisDiagnostic,
    DependencyFact, DescriptorFact, PyValue,
};
use crate::parsers::common::{evidence, module_path_for_file};
use crate::parsers::DescriptorParseOutput;

static PROPERTY_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\$\{([^}]+)\}").expect("regex"));

fn local<'a>(element: &'a EtNode, name: &str) -> Option<&'a EtNode> {
    element
        .children
        .iter()
        .find(|child| child.tag.rsplit('}').next().unwrap_or(&child.tag) == name)
}

fn text_of(element: Option<&EtNode>, default: &str) -> String {
    match element {
        Some(node) => node.text.trim().to_string(),
        None => default.to_string(),
    }
}

fn resolve(value: &str, properties: &[(String, String)]) -> String {
    PROPERTY_RE
        .replace_all(value, |caps: &regex::Captures| {
            let key = caps.get(1).map(|m| m.as_str()).unwrap_or("");
            properties
                .iter()
                .find(|(name, _)| name == key)
                .map(|(_, value)| value.clone())
                .unwrap_or_else(|| caps.get(0).unwrap().as_str().to_string())
        })
        .into_owned()
}

fn scope_of(value: &str) -> &'static str {
    match value.to_lowercase().as_str() {
        "test" => dependency_scope::TEST,
        "runtime" => dependency_scope::RUNTIME,
        "provided" => dependency_scope::PROVIDED,
        "compile" => dependency_scope::COMPILE,
        _ => dependency_scope::UNKNOWN,
    }
}

fn unsafe_descriptor(
    project_id: &str,
    path: &str,
    module_path: &str,
    summary: &str,
    diagnostic: AnalysisDiagnostic,
) -> DescriptorParseOutput {
    let descriptor = DescriptorFact::create(
        project_id,
        module_path,
        path,
        descriptor_type::MAVEN_POM,
        descriptor_role::IDENTITY,
        "maven",
        parse_depth::IDENTITY,
        summary.to_string(),
        PyValue::dict(),
        confidence::LOW,
        evidence(path),
        vec![diagnostic.clone()],
    )
    .expect("maven error descriptor");
    DescriptorParseOutput {
        descriptor,
        dependencies: vec![],
        endpoints: vec![],
        diagnostics: vec![diagnostic],
    }
}

pub fn parse_maven(project_id: &str, path: &str, text: &str) -> DescriptorParseOutput {
    let module_path = module_path_for_file(path);
    let upper = text.to_uppercase();
    if upper.contains("<!DOCTYPE") || upper.contains("<!ENTITY") {
        return unsafe_descriptor(
            project_id,
            path,
            &module_path,
            "Unsafe Maven descriptor rejected",
            AnalysisDiagnostic::new(
                crate::models::diagnostic_code::XML_UNSAFE,
                "DOCTYPE and entity declarations are not permitted in descriptor XML.",
            )
            .severity("error")
            .file_path(path)
            .module_path(&module_path),
        );
    }
    let root = match crate::etdom::parse_document(text) {
        Ok(root) => root,
        Err(error) => {
            return unsafe_descriptor(
                project_id,
                path,
                &module_path,
                "Malformed Maven descriptor",
                AnalysisDiagnostic::new(
                    crate::models::diagnostic_code::MALFORMED_DESCRIPTOR,
                    &format!("Malformed Maven POM: {}", error.message),
                )
                .severity("error")
                .file_path(path)
                .module_path(&module_path),
            );
        }
    };

    let mut properties: Vec<(String, String)> = Vec::new();
    if let Some(properties_node) = local(&root, "properties") {
        for child in &properties_node.children {
            let key = child.tag.rsplit('}').next().unwrap_or(&child.tag).to_string();
            let value = child.text.trim().to_string();
            properties.push((key, value));
        }
    }
    let parent = local(&root, "parent");
    let parent_group = match parent.and_then(|node| local(node, "groupId")) {
        Some(node) => node.text.trim().to_string(),
        None => String::new(),
    };
    let parent_version = match parent.and_then(|node| local(node, "version")) {
        Some(node) => node.text.trim().to_string(),
        None => String::new(),
    };
    let group_id = resolve(&text_of(local(&root, "groupId"), &parent_group), &properties);
    let artifact_id = resolve(&text_of(local(&root, "artifactId"), ""), &properties);
    let version = resolve(&text_of(local(&root, "version"), &parent_version), &properties);
    let packaging = text_of(local(&root, "packaging"), "jar");
    let mut declared_modules: Vec<String> = Vec::new();
    if let Some(modules_node) = local(&root, "modules") {
        for item in local_children(modules_node, "module") {
            let target = item.text.trim().to_string();
            if !target.is_empty() {
                let joined = if module_path != "." {
                    format!("{module_path}/{target}")
                } else {
                    target.clone()
                };
                declared_modules.push(normalize_module_path(&joined).unwrap_or_else(|_| ".".to_string()));
            }
        }
    }

    let mut diagnostics: Vec<AnalysisDiagnostic> = Vec::new();
    let mut dependencies: Vec<DependencyFact> = Vec::new();
    if let Some(dependencies_node) = local(&root, "dependencies") {
        for item in local_children(dependencies_node, "dependency") {
            let dep_group = resolve(&text_of(local(item, "groupId"), ""), &properties);
            let dep_artifact = resolve(&text_of(local(item, "artifactId"), ""), &properties);
            let dep_version = resolve(&text_of(local(item, "version"), ""), &properties);
            let dep_scope = scope_of(&text_of(local(item, "scope"), "compile"));
            let target = [dep_group.as_str(), dep_artifact.as_str(), dep_version.as_str()]
                .iter()
                .filter(|value| !value.is_empty())
                .copied()
                .collect::<Vec<&str>>()
                .join(":");
            let mut fact_properties = PyValue::dict();
            fact_properties.set("group_id", PyValue::Str(dep_group.clone()));
            fact_properties.set("artifact_id", PyValue::Str(dep_artifact.clone()));
            if let Ok(dependency) = DependencyFact::create(
                project_id,
                &module_path,
                &target,
                dep_scope,
                None,
                false,
                path,
                evidence(path),
                fact_properties,
            ) {
                dependencies.push(dependency);
            }
            if target.contains("${") {
                diagnostics.push(
                    AnalysisDiagnostic::new(
                        crate::models::diagnostic_code::UNRESOLVED_REFERENCE,
                        &format!("Unresolved Maven property in dependency {target}."),
                    )
                    .file_path(path)
                    .module_path(&module_path),
                );
            }
        }
    }
    dependencies.sort_by(|a, b| a.id.cmp(&b.id));
    let module_kind_value = if ["pom", "jar", "war", "ear"].contains(&packaging.as_str()) {
        module_kind::MAVEN_MODULE
    } else {
        module_kind::UNKNOWN
    };
    let mut properties = PyValue::dict();
    properties.set("group_id", PyValue::Str(group_id.clone()));
    properties.set("artifact_id", PyValue::Str(artifact_id.clone()));
    properties.set("version", PyValue::Str(version.clone()));
    properties.set(
        "coordinate",
        PyValue::Str(
            [group_id.as_str(), artifact_id.as_str(), version.as_str()]
                .iter()
                .filter(|value| !value.is_empty())
                .copied()
                .collect::<Vec<&str>>()
                .join(":"),
        ),
    );
    properties.set("packaging", PyValue::Str(packaging.clone()));
    properties.set("module_kind", PyValue::Str(module_kind_value.to_string()));
    properties.set(
        "declared_modules",
        PyValue::List(
            sorted_unique(declared_modules)
                .into_iter()
                .map(PyValue::Str)
                .collect(),
        ),
    );
    properties.set("build_system", PyValue::Str("maven".to_string()));
    let summary = safe_summary(&format!(
        "Maven {group_id}:{artifact_id}:{version} ({packaging})"
    ));
    let descriptor = DescriptorFact::create(
        project_id,
        &module_path,
        path,
        descriptor_type::MAVEN_POM,
        descriptor_role::DEPENDENCY,
        "maven",
        parse_depth::DEPENDENCY,
        summary,
        properties,
        if diagnostics.is_empty() { confidence::HIGH } else { confidence::MEDIUM },
        evidence(path),
        diagnostics.clone(),
    )
    .expect("maven descriptor");
    DescriptorParseOutput {
        descriptor,
        dependencies,
        endpoints: vec![],
        diagnostics,
    }
}

fn local_children<'a>(element: &'a EtNode, name: &str) -> Vec<&'a EtNode> {
    element
        .children
        .iter()
        .filter(|child| child.tag.rsplit('}').next().unwrap_or(&child.tag) == name)
        .collect()
}

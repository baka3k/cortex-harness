//! Port `parsers/android.py` — secure Android manifest/resource XML extraction.
//! `_REFERENCE_RE` dùng lookbehind `(?<![\w])` — fancy-regex.

use std::sync::LazyLock;

use fancy_regex::Regex as FancyRegex;
use regex::Regex;

use crate::etdom::EtNode;
use crate::models::{
    confidence, descriptor_role, descriptor_type, parse_depth, safe_summary, AnalysisDiagnostic,
    DescriptorFact, PyValue,
};
use crate::parsers::common::{evidence, module_path_for_file, MAX_XML_DEPTH, MAX_XML_NODES};
use crate::parsers::DescriptorParseOutput;

const ANDROID_NS: &str = "{http://schemas.android.com/apk/res/android}";

static REFERENCE_RE: LazyLock<FancyRegex> = LazyLock::new(|| {
    FancyRegex::new(r"(?<![\w])([@?][+*]?[A-Za-z0-9_.:-]+/[A-Za-z0-9_.-]+)").expect("regex")
});
static PLACEHOLDER_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\$\{[^}]+\}").expect("regex"));
static SECRET_NAME_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(?:api[_-]?key|secret|token|password|credential|private[_-]?key)").expect("regex")
});

fn android(node: &EtNode, name: &str) -> String {
    node.attr(&format!("{ANDROID_NS}{name}"))
        .unwrap_or_default()
        .to_string()
}

fn safe_xml(path: &str, text: &str, module_path: &str) -> (Option<EtNode>, Vec<AnalysisDiagnostic>) {
    let upper = text.to_uppercase();
    if upper.contains("<!DOCTYPE") || upper.contains("<!ENTITY") {
        return (
            None,
            vec![AnalysisDiagnostic::new(
                crate::models::diagnostic_code::XML_UNSAFE,
                "DOCTYPE and entity declarations are not permitted in Android XML.",
            )
            .severity("error")
            .file_path(path)
            .module_path(module_path)],
        );
    }
    let root = match crate::etdom::parse_document(text) {
        Ok(root) => root,
        Err(error) => {
            return (
                None,
                vec![AnalysisDiagnostic::new(
                    crate::models::diagnostic_code::MALFORMED_DESCRIPTOR,
                    &format!("Malformed Android XML: {}", error.message),
                )
                .severity("error")
                .file_path(path)
                .module_path(module_path)],
            );
        }
    };
    let mut count = 0usize;
    let mut max_depth = 0usize;
    let mut stack = vec![(root.clone(), 1usize)];
    while let Some((node, depth)) = stack.pop() {
        count += 1;
        max_depth = max_depth.max(depth);
        if count > MAX_XML_NODES || max_depth > MAX_XML_DEPTH {
            let mut details = PyValue::dict();
            details.set("nodes", PyValue::Int(count as i64));
            details.set("depth", PyValue::Int(max_depth as i64));
            details.set("node_limit", PyValue::Int(MAX_XML_NODES as i64));
            details.set("depth_limit", PyValue::Int(MAX_XML_DEPTH as i64));
            return (
                None,
                vec![AnalysisDiagnostic::new(
                    crate::models::diagnostic_code::LIMIT_EXCEEDED,
                    "Android XML exceeds the safe node/depth limit.",
                )
                .severity("error")
                .file_path(path)
                .module_path(module_path)
                .details(details)],
            );
        }
        for child in node.children.iter().rev() {
            stack.push((child.clone(), depth + 1));
        }
    }
    (Some(root), vec![])
}

fn source_set(path: &str) -> String {
    let parts: Vec<&str> = path.split('/').collect();
    match parts.iter().position(|part| *part == "src") {
        Some(index) if index + 1 < parts.len() => parts[index + 1].to_string(),
        _ => String::new(),
    }
}

fn local_tag(tag: &str) -> &str {
    tag.rsplit('}').next().unwrap_or(tag)
}

pub fn parse_android_manifest(project_id: &str, path: &str, text: &str) -> DescriptorParseOutput {
    let mut module_path = module_path_for_file(path);
    if path.contains("/src/") {
        let prefix = path.split("/src/").next().unwrap_or("");
        module_path = if prefix.is_empty() {
            ".".to_string()
        } else {
            prefix.to_string()
        };
    }
    let (root, xml_diagnostics) = safe_xml(path, text, &module_path);
    let mut diagnostics = xml_diagnostics;
    let mut properties = PyValue::dict();
    properties.set("source_set", PyValue::Str(source_set(path)));
    properties.set("permissions", PyValue::List(Vec::new()));
    properties.set("features", PyValue::List(Vec::new()));
    properties.set("queries", PyValue::List(Vec::new()));
    properties.set("instrumentation", PyValue::List(Vec::new()));
    properties.set("metadata_keys", PyValue::List(Vec::new()));
    properties.set("components", PyValue::List(Vec::new()));
    let mut component_count = 0usize;
    if let Some(root_node) = &root {
        properties.set(
            "package",
            PyValue::Str(root_node.attr("package").unwrap_or_default().to_string()),
        );
        properties.set("version_code", PyValue::Str(android(root_node, "versionCode")));
        properties.set("version_name", PyValue::Str(android(root_node, "versionName")));
        if let Some(uses_sdk) = root_node.find("uses-sdk") {
            let mut sdk = PyValue::dict();
            sdk.set("min", PyValue::Str(android(uses_sdk, "minSdkVersion")));
            sdk.set("target", PyValue::Str(android(uses_sdk, "targetSdkVersion")));
            sdk.set("max", PyValue::Str(android(uses_sdk, "maxSdkVersion")));
            properties.set("sdk", sdk);
        }
        for node in &root_node.children {
            let tag = local_tag(&node.tag);
            if tag == "uses-permission" || tag == "permission" {
                let mut item = PyValue::dict();
                item.set("name", PyValue::Str(android(node, "name")));
                item.set("kind", PyValue::Str(tag.to_string()));
                item.set("max_sdk", PyValue::Str(android(node, "maxSdkVersion")));
                push_list(&mut properties, "permissions", item);
            } else if tag == "uses-feature" {
                let mut item = PyValue::dict();
                item.set("name", PyValue::Str(android(node, "name")));
                item.set("required", PyValue::Str(android(node, "required")));
                item.set("gl_es_version", PyValue::Str(android(node, "glEsVersion")));
                push_list(&mut properties, "features", item);
            } else if tag == "queries" {
                for query in &node.children {
                    let mut item = PyValue::dict();
                    item.set(
                        "kind",
                        PyValue::Str(local_tag(&query.tag).to_string()),
                    );
                    item.set("name", PyValue::Str(android(query, "name")));
                    item.set(
                        "package",
                        PyValue::Str(query.attr("package").unwrap_or_default().to_string()),
                    );
                    push_list(&mut properties, "queries", item);
                }
            } else if tag == "instrumentation" {
                let mut item = PyValue::dict();
                item.set("name", PyValue::Str(android(node, "name")));
                item.set("target_package", PyValue::Str(android(node, "targetPackage")));
                push_list(&mut properties, "instrumentation", item);
            }
        }
        if let Some(application) = root_node.find("application") {
            let mut application_props = PyValue::dict();
            for name in [
                "name",
                "label",
                "theme",
                "icon",
                "debuggable",
                "allowBackup",
                "networkSecurityConfig",
            ] {
                let value = android(application, name);
                if !value.is_empty() {
                    application_props.set(name, PyValue::Str(value));
                }
            }
            properties.set("application", application_props);
            for metadata in application.find_all("meta-data") {
                let name = android(metadata, "name");
                if !name.is_empty() {
                    push_list(&mut properties, "metadata_keys", PyValue::Str(name));
                }
            }
            for node in &application.children {
                let tag = local_tag(&node.tag);
                if ![
                    "activity",
                    "activity-alias",
                    "service",
                    "receiver",
                    "provider",
                ]
                .contains(&tag)
                {
                    continue;
                }
                component_count += 1;
                let mut component = PyValue::dict();
                component.set("kind", PyValue::Str(tag.to_string()));
                component.set("name", PyValue::Str(android(node, "name")));
                component.set("target_activity", PyValue::Str(android(node, "targetActivity")));
                let exported = android(node, "exported");
                component.set(
                    "exported",
                    PyValue::Str(if exported.is_empty() { "unknown".to_string() } else { exported }),
                );
                let enabled = android(node, "enabled");
                component.set(
                    "enabled",
                    PyValue::Str(if enabled.is_empty() { "default".to_string() } else { enabled }),
                );
                component.set("permission", PyValue::Str(android(node, "permission")));
                component.set("process", PyValue::Str(android(node, "process")));
                component.set("intent_filters", PyValue::List(Vec::new()));
                for intent_filter in node.find_all("intent-filter") {
                    let mut item = PyValue::dict();
                    let actions: Vec<PyValue> = intent_filter
                        .find_all("action")
                        .iter()
                        .map(|child| android(child, "name"))
                        .filter(|name| !name.is_empty())
                        .map(PyValue::Str)
                        .collect();
                    let categories: Vec<PyValue> = intent_filter
                        .find_all("category")
                        .iter()
                        .map(|child| android(child, "name"))
                        .filter(|name| !name.is_empty())
                        .map(PyValue::Str)
                        .collect();
                    item.set("actions", PyValue::List(actions));
                    item.set("categories", PyValue::List(categories));
                    let mut data_list: Vec<PyValue> = Vec::new();
                    for data in intent_filter.find_all("data") {
                        let mut data_item = PyValue::dict();
                        for key in [
                            "scheme",
                            "host",
                            "port",
                            "path",
                            "pathPrefix",
                            "pathPattern",
                            "mimeType",
                        ] {
                            let value = android(data, key);
                            if !value.is_empty() {
                                data_item.set(key, PyValue::Str(value));
                            }
                        }
                        data_list.push(data_item);
                    }
                    item.set("data", PyValue::List(data_list));
                    push_list(&mut component, "intent_filters", item);
                }
                push_list(&mut properties, "components", component);
            }
        }
        if PLACEHOLDER_RE.is_match(text) {
            diagnostics.push(
                AnalysisDiagnostic::new(
                    crate::models::diagnostic_code::UNRESOLVED_REFERENCE,
                    "Manifest placeholders were retained without build-time resolution.",
                )
                .file_path(path)
                .module_path(&module_path),
            );
        }
    }
    let parse_depth_value = if root.is_some() {
        parse_depth::SEMANTIC
    } else {
        parse_depth::IDENTITY
    };
    let confidence_value = if root.is_none() {
        confidence::LOW
    } else if !diagnostics.is_empty() {
        confidence::MEDIUM
    } else {
        confidence::HIGH
    };
    let summary = safe_summary(&format!(
        "Android manifest with {component_count} components"
    ));
    let descriptor = DescriptorFact::create(
        project_id,
        &module_path,
        path,
        descriptor_type::ANDROID_MANIFEST,
        descriptor_role::FRAMEWORK,
        "android_manifest",
        parse_depth_value,
        summary,
        properties,
        confidence_value,
        evidence(path),
        diagnostics.clone(),
    )
    .expect("android manifest descriptor");
    DescriptorParseOutput {
        descriptor,
        dependencies: vec![],
        endpoints: vec![],
        diagnostics,
    }
}

fn push_list(properties: &mut PyValue, key: &str, item: PyValue) {
    if let Some(PyValue::List(items)) = properties.get_mut(key) {
        items.push(item);
    }
}

fn qualifier(path: &str) -> String {
    for part in path.split('/') {
        for prefix in ["values", "layout", "navigation", "menu", "xml", "drawable"] {
            if part.starts_with(prefix) {
                return part.to_string();
            }
        }
    }
    String::new()
}

pub fn parse_android_resource(project_id: &str, path: &str, text: &str) -> DescriptorParseOutput {
    let module_path = if path.contains("/src/") {
        path.split("/src/").next().unwrap_or("").to_string()
    } else {
        module_path_for_file(path)
    };
    let (root, xml_diagnostics) = safe_xml(path, text, &module_path);
    let mut diagnostics = xml_diagnostics;
    let mut values: Vec<PyValue> = Vec::new();
    let mut views: Vec<PyValue> = Vec::new();
    let references = {
        let mut collected: Vec<String> = REFERENCE_RE
            .find_iter(text)
            .flatten()
            .map(|matched| matched.as_str().to_string())
            .collect();
        collected.sort();
        collected.dedup();
        collected
    };
    let mut redacted = false;
    if let Some(root_node) = &root {
        if local_tag(&root_node.tag) == "resources" {
            for node in &root_node.children {
                let resource_type = local_tag(&node.tag).to_string();
                let name = node.attr("name").unwrap_or_default().to_string();
                let secret = SECRET_NAME_RE.is_match(&name);
                redacted = redacted || secret;
                let mut item = PyValue::dict();
                item.set("type", PyValue::Str(resource_type));
                item.set("name", PyValue::Str(name));
                item.set(
                    "parent",
                    PyValue::Str(node.attr("parent").unwrap_or_default().to_string()),
                );
                item.set("item_count", PyValue::Int(node.children.len() as i64));
                let value = if secret {
                    "[redacted]".to_string()
                } else {
                    safe_summary_limited_120(&node.text)
                };
                item.set("value", PyValue::Str(value));
                values.push(item);
            }
        } else {
            let mut stack = vec![(root_node.clone(), String::new())];
            while let Some((node, parent_id)) = stack.pop() {
                let node_id = android(&node, "id");
                let mut item = PyValue::dict();
                item.set("type", PyValue::Str(local_tag(&node.tag).to_string()));
                item.set("id", PyValue::Str(node_id.clone()));
                item.set("parent_id", PyValue::Str(parent_id.clone()));
                item.set("name", PyValue::Str(android(&node, "name")));
                item.set("destination", PyValue::Str(android(&node, "destination")));
                item.set("uri", PyValue::Str(android(&node, "uri")));
                views.push(item);
                let mut children: Vec<EtNode> = node.children.clone();
                children.reverse();
                for child in children {
                    let carried = if node_id.is_empty() { parent_id.clone() } else { node_id.clone() };
                    stack.push((child, carried));
                }
            }
        }
    }
    if redacted {
        diagnostics.push(
            AnalysisDiagnostic::new(
                crate::models::diagnostic_code::SECRET_REDACTED,
                "Secret-like Android resource values were redacted.",
            )
            .file_path(path)
            .module_path(&module_path),
        );
    }
    let mut properties = PyValue::dict();
    properties.set("qualifier", PyValue::Str(qualifier(path)));
    properties.set("values", PyValue::List(values.clone()));
    properties.set("views", PyValue::List(views.clone()));
    properties.set(
        "references",
        PyValue::List(references.into_iter().map(PyValue::Str).collect()),
    );
    let parse_depth_value = if root.is_some() {
        parse_depth::SEMANTIC
    } else {
        parse_depth::IDENTITY
    };
    let summary = safe_summary(&format!(
        "Android resource {path} with {} facts",
        if values.is_empty() { views.len() } else { values.len() }
    ));
    let descriptor = DescriptorFact::create(
        project_id,
        &module_path,
        path,
        descriptor_type::RESOURCE,
        descriptor_role::RESOURCE,
        "android_resource",
        parse_depth_value,
        summary,
        properties,
        if root.is_none() { confidence::LOW } else { confidence::HIGH },
        evidence(path),
        diagnostics.clone(),
    )
    .expect("android resource descriptor");
    let descriptor = DescriptorFact {
        secret_bearing: redacted,
        redacted,
        ..descriptor
    };
    DescriptorParseOutput {
        descriptor,
        dependencies: vec![],
        endpoints: vec![],
        diagnostics,
    }
}

fn safe_summary_limited_120(value: &str) -> String {
    crate::models::safe_summary_limited(value, 120)
}

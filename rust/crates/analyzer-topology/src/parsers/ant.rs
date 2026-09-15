//! Port `parsers/ant.py` — static Ant project/target extraction.

use crate::models::{
    confidence, descriptor_role, descriptor_type, parse_depth, safe_summary, sorted_unique,
    AnalysisDiagnostic, DescriptorFact, PyValue,
};
use crate::parsers::common::{evidence, module_path_for_file};
use crate::parsers::DescriptorParseOutput;

fn local_tag(tag: &str) -> &str {
    tag.rsplit('}').next().unwrap_or(tag)
}

pub fn parse_ant(project_id: &str, path: &str, text: &str) -> DescriptorParseOutput {
    let module_path = module_path_for_file(path);
    let mut diagnostics: Vec<AnalysisDiagnostic> = Vec::new();
    let upper = text.to_uppercase();
    let root = if upper.contains("<!DOCTYPE") || upper.contains("<!ENTITY") {
        diagnostics.push(
            AnalysisDiagnostic::new(
                crate::models::diagnostic_code::XML_UNSAFE,
                "DOCTYPE and entity declarations are not permitted in Ant XML.",
            )
            .severity("error")
            .file_path(path)
            .module_path(&module_path),
        );
        None
    } else {
        match crate::etdom::parse_document(text) {
            Ok(root) => Some(root),
            Err(error) => {
                diagnostics.push(
                    AnalysisDiagnostic::new(
                        crate::models::diagnostic_code::MALFORMED_DESCRIPTOR,
                        &format!("Malformed Ant build file: {}", error.message),
                    )
                    .severity("error")
                    .file_path(path)
                    .module_path(&module_path),
                );
                None
            }
        }
    };
    let mut targets: Vec<PyValue> = Vec::new();
    let mut imports: Vec<String> = Vec::new();
    let mut project_name = String::new();
    if let Some(root_node) = &root {
        project_name = root_node.attr("name").unwrap_or_default().to_string();
        for node in root_node.iter_nodes() {
            let local = local_tag(&node.tag);
            if local == "target" {
                let target = node.attr("name").unwrap_or_default().to_string();
                let depends: Vec<PyValue> = node
                    .attr("depends")
                    .unwrap_or_default()
                    .split(',')
                    .map(|item| item.trim().to_string())
                    .filter(|item| !item.is_empty())
                    .map(PyValue::Str)
                    .collect();
                if !target.is_empty() {
                    let mut entry = PyValue::dict();
                    entry.set("name", PyValue::Str(target));
                    entry.set("depends", PyValue::List(depends));
                    targets.push(entry);
                }
            } else if local == "import"
                && let Some(file) = node.attr("file")
            {
                imports.push(file.to_string());
            }
        }
    }
    let parse_depth_value = if root.is_some() {
        parse_depth::TOPOLOGY
    } else {
        parse_depth::IDENTITY
    };
    let mut properties = PyValue::dict();
    properties.set("project_name", PyValue::Str(project_name.clone()));
    properties.set("targets", PyValue::List(targets));
    properties.set(
        "imports",
        PyValue::List(
            sorted_unique(imports)
                .into_iter()
                .map(PyValue::Str)
                .collect(),
        ),
    );
    properties.set("build_system", PyValue::Str("ant".to_string()));
    let summary = safe_summary(&format!(
        "Ant project {}",
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
        descriptor_type::ANT_BUILD,
        descriptor_role::TOPOLOGY,
        "ant",
        parse_depth_value,
        summary,
        properties,
        if root.is_none() { confidence::LOW } else { confidence::HIGH },
        evidence(path),
        diagnostics.clone(),
    )
    .expect("ant descriptor");
    DescriptorParseOutput {
        descriptor,
        dependencies: vec![],
        endpoints: vec![],
        diagnostics,
    }
}

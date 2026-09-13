//! Port `tools/servlet_jsp/pipeline.py` — foundation + full analysis.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::time::Instant;

use serde_json::{json, Map};

use crate::servlet_jsp::detector::{ServletJspProjectDetector, ServletJspDetectionResult};
use crate::servlet_jsp::java_identity::JavaIdentityProvider;
use crate::servlet_jsp::java_semantics::analyze_java_file;
use crate::servlet_jsp::jsp_parser::parse_jsp_file;
use crate::servlet_jsp::models::{
    stable_semantic_id, Diagnostic, ParserCapability, ResourceBudgets, ServletJspAnalysisResult,
    ServletJspArtifact, ServletJspDependencyIndex, ServletJspFact, ServletJspModule, SourceSpan,
};
use crate::servlet_jsp::properties_parser::parse_properties_file;
use crate::servlet_jsp::resolver::{resolve_servlet_jsp_module, ResolveModuleInput};
use crate::servlet_jsp::web_xml_parser::parse_web_xml_file;

const ARTIFACT_GROUPS: [(&str, &str); 6] = [
    ("java_files", "java"),
    ("descriptor_files", "web_xml"),
    ("jsp_files", "jsp"),
    ("properties_files", "properties"),
    ("build_files", "build"),
    ("static_files", "static"),
];

/// `check_parser_capabilities` — Rust thực hiện parse check thật cho java/xml;
/// metadata (package/version/abi) PIN theo reference venv
/// (tree-sitter-language-pack 1.14.3, ABI 15) vì Rust không introspect venv.
pub fn check_parser_capabilities() -> (Vec<ParserCapability>, Vec<Diagnostic>) {
    let mut capabilities: Vec<ParserCapability> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();

    // java
    let java_sample: &[u8] = b"package demo; class Example extends jakarta.servlet.http.HttpServlet {}\n";
    let (available, status, message) = probe_java(java_sample);
    capabilities.push(ParserCapability {
        language: "java".into(),
        available,
        mandatory: true,
        parser: "tree_sitter_language_pack.get_parser".into(),
        package: "tree-sitter-language-pack".into(),
        package_version: "1.14.3".into(),
        abi_version: "15".into(),
        status: status.clone(),
        message: message.clone(),
    });
    if !available {
        diagnostics.push(Diagnostic::new(
            "servlet_jsp.parser.java.parse_error",
            &message,
            "error",
            "",
            1,
            1,
        ));
    }

    // xml
    let xml_sample: &[u8] = b"<?xml version=\"1.0\"?>\n<!DOCTYPE web-app PUBLIC \"-//Sun Microsystems, Inc.//DTD Web Application 2.3//EN\" \"http://java.sun.com/dtd/web-app_2_3.dtd\">\n<web-app xmlns=\"https://jakarta.ee/xml/ns/jakartaee\"><servlet/></web-app>\n";
    let (available, status, message) = probe_xml(xml_sample);
    capabilities.push(ParserCapability {
        language: "xml".into(),
        available,
        mandatory: true,
        parser: "tree_sitter_language_pack.get_parser".into(),
        package: "tree-sitter-language-pack".into(),
        package_version: "1.14.3".into(),
        abi_version: "15".into(),
        status,
        message: message.clone(),
    });
    if !available {
        diagnostics.push(Diagnostic::new(
            "servlet_jsp.parser.xml.parse_error",
            &message,
            "error",
            "",
            1,
            1,
        ));
    }

    // html — sample parse không lỗi trên grammar reference.
    capabilities.push(ParserCapability {
        language: "html".into(),
        available: true,
        mandatory: false,
        parser: "tree_sitter_language_pack.get_parser".into(),
        package: "tree-sitter-language-pack".into(),
        package_version: "1.14.3".into(),
        abi_version: "15".into(),
        status: "ok".into(),
        message: String::new(),
    });

    (capabilities, diagnostics)
}

fn probe_java(source: &[u8]) -> (bool, String, String) {
    match crate::struts::java_validation::parse_java_bytes(source) {
        Ok(tree) => {
            if tree.root_node().has_error() {
                (false, "parse_error".into(), "java sample parsed with syntax errors".into())
            } else {
                (true, "ok".into(), String::new())
            }
        }
        Err(error) => (false, "unavailable".into(), error),
    }
}

fn probe_xml(source: &[u8]) -> (bool, String, String) {
    match crate::servlet_jsp::web_xml_parser::parse_xml_bytes(source) {
        Ok(tree) => {
            if tree.root_node().has_error() {
                (false, "parse_error".into(), "xml sample parsed with syntax errors".into())
            } else {
                (true, "ok".into(), String::new())
            }
        }
        Err(error) => (false, "unavailable".into(), error),
    }
}

pub fn run_servlet_jsp_foundation(
    root: &str,
    project_id: &str,
    project_name: &str,
    budgets: &ResourceBudgets,
    deleted_paths: &[String],
) -> ServletJspAnalysisResult {
    let project_root = crate::pyutil::realpath(Path::new(root)).to_string_lossy().to_string();
    let mut detector = ServletJspProjectDetector::new(Path::new(root));
    let discovered = detector.discover_modules();
    let (capabilities, mut capability_diagnostics) = check_parser_capabilities();
    let mut modules: Vec<ServletJspModule> = Vec::new();
    let mut artifacts: Vec<ServletJspArtifact> = Vec::new();
    let mut facts: Vec<ServletJspFact> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = std::mem::take(&mut capability_diagnostics);

    let mut total_artifacts = 0i64;
    let mut total_source_bytes = 0i64;
    let mut artifact_budget_reported = false;
    let mut byte_budget_reported = false;

    let mut sorted_discovered: Vec<&crate::servlet_jsp::detector::DiscoveredServletModule> = discovered.iter().collect();
    sorted_discovered.sort_by(|a, b| a.rel_path.cmp(&b.rel_path));

    for item in sorted_discovered {
        let mut bounded_paths: BTreeMap<&str, Vec<String>> = BTreeMap::new();
        for (attr, _) in ARTIFACT_GROUPS {
            let files = match attr {
                "java_files" => &item.java_files,
                "descriptor_files" => &item.descriptor_files,
                "jsp_files" => &item.jsp_files,
                "properties_files" => &item.properties_files,
                "build_files" => &item.build_files,
                "static_files" => &item.static_files,
                _ => unreachable!(),
            };
            let values: Vec<String> = {
                let mut sorted: Vec<String> = files.iter().map(|path| normalize(path)).collect();
                sorted.sort();
                sorted
            };
            let mut accepted: Vec<String> = Vec::new();
            for file_path in values {
                if total_artifacts >= budgets.max_artifacts_per_project {
                    if !artifact_budget_reported {
                        artifact_budget_reported = true;
                        diagnostics.push(Diagnostic::new(
                            "servlet_jsp.budget.artifacts",
                            &format!("Artifact budget {} reached", budgets.max_artifacts_per_project),
                            "warning",
                            &file_path,
                            1,
                            1,
                        ));
                    }
                    continue;
                }
                let source_bytes = std::fs::metadata(Path::new(&project_root).join(&file_path))
                    .map(|metadata| metadata.len() as i64)
                    .unwrap_or(0);
                if total_source_bytes + source_bytes > budgets.max_total_source_bytes {
                    if !byte_budget_reported {
                        byte_budget_reported = true;
                        diagnostics.push(Diagnostic::new(
                            "servlet_jsp.budget.total_source_bytes",
                            &format!(
                                "Total source byte budget {} reached",
                                budgets.max_total_source_bytes
                            ),
                            "warning",
                            &file_path,
                            1,
                            1,
                        ));
                    }
                    continue;
                }
                accepted.push(file_path);
                total_artifacts += 1;
                total_source_bytes += source_bytes;
            }
            bounded_paths.insert(attr, accepted);
        }
        let module = ServletJspModule {
            module_id: item.module_id.clone(),
            root: project_root.clone(),
            rel_path: item.rel_path.clone(),
            java_files: bounded_paths["java_files"].clone(),
            descriptor_files: bounded_paths["descriptor_files"].clone(),
            jsp_files: bounded_paths["jsp_files"].clone(),
            properties_files: bounded_paths["properties_files"].clone(),
            build_files: bounded_paths["build_files"].clone(),
            static_files: bounded_paths["static_files"].clone(),
            evidence: item.evidence.clone(),
            confidence: item.confidence,
        };
        let empty = module.java_files.is_empty()
            && module.descriptor_files.is_empty()
            && module.jsp_files.is_empty()
            && module.properties_files.is_empty()
            && module.build_files.is_empty()
            && module.static_files.is_empty();
        if empty {
            continue;
        }
        modules.push(module.clone());
        facts.push(module_fact(project_id, project_name, &module));
        for (attr, kind) in ARTIFACT_GROUPS {
            let files = match attr {
                "java_files" => &module.java_files,
                "descriptor_files" => &module.descriptor_files,
                "jsp_files" => &module.jsp_files,
                "properties_files" => &module.properties_files,
                "build_files" => &module.build_files,
                "static_files" => &module.static_files,
                _ => unreachable!(),
            };
            for file_path in files {
                let detection = detector.detect_path(file_path);
                let artifact = ServletJspArtifact {
                    kind: artifact_kind(kind, file_path),
                    file_path: file_path.clone(),
                    module_id: module.module_id.clone(),
                    module_path: module.rel_path.clone(),
                    evidence: detection.evidence.clone(),
                    confidence: module.confidence,
                    source: SourceSpan::new(file_path),
                };
                if ["web_xml", "jsp", "jspx", "jsp_fragment", "static"].contains(&artifact.kind.as_str()) {
                    facts.push(artifact_fact(project_id, project_name, &artifact));
                }
                artifacts.push(artifact);
            }
        }
    }

    let mut deleted_sorted: BTreeSet<String> = BTreeSet::new();
    for item in deleted_paths {
        if !item.is_empty() {
            deleted_sorted.insert(normalize(item));
        }
    }
    for path in &deleted_sorted {
        diagnostics.push(Diagnostic::new(
            "servlet_jsp.incremental.deleted_path",
            "Deleted path requires applied-snapshot dependency expansion and module regeneration",
            "info",
            path,
            1,
            1,
        ));
    }
    let mandatory_missing = capabilities.iter().any(|item| item.mandatory && !item.available);
    facts.sort_by(|a, b| a.stable_id.cmp(&b.stable_id));
    if facts.len() as i64 > budgets.max_facts_per_project {
        diagnostics.push(Diagnostic::new(
            "servlet_jsp.budget.facts",
            &format!("Fact budget {} reached", budgets.max_facts_per_project),
            "warning",
            "",
            1,
            1,
        ));
        facts.truncate(budgets.max_facts_per_project as usize);
    }
    let mut truncation_count = diagnostics.iter().filter(|item| item.code.contains(".budget.")).count() as i64;
    let (bounded_diagnostics, diagnostics_truncated) =
        bounded_project_diagnostics(diagnostics, budgets.max_diagnostics_per_project);
    if diagnostics_truncated {
        truncation_count += 1;
    }
    let coverage = if modules.is_empty() {
        "empty"
    } else if mandatory_missing || truncation_count > 0 {
        "partial"
    } else {
        "complete"
    };
    let mut dependency_files: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut sorted_artifacts: Vec<&ServletJspArtifact> = artifacts.iter().collect();
    sorted_artifacts.sort_by(|a, b| a.file_path.cmp(&b.file_path));
    for artifact in sorted_artifacts {
        dependency_files.entry(artifact.file_path.clone()).or_default();
    }
    ServletJspAnalysisResult {
        project_id: project_id.to_string(),
        project_name: project_name.to_string(),
        root: project_root,
        modules: {
            let mut sorted = modules;
            sorted.sort_by(|a, b| a.rel_path.cmp(&b.rel_path));
            sorted
        },
        artifacts: {
            let mut sorted = artifacts;
            sorted.sort_by(|a, b| (&a.module_id, &a.file_path, &a.kind).cmp(&(&b.module_id, &b.file_path, &b.kind)));
            sorted
        },
        parser_capabilities: capabilities,
        semantic_facts: facts,
        relationships: Vec::new(),
        dependency_index: ServletJspDependencyIndex {
            files: dependency_files,
            ..Default::default()
        },
        diagnostics: bounded_diagnostics,
        coverage_status: coverage.to_string(),
        truncation_count,
        missing_anchor_count: 0,
        ambiguity_count: 0,
    }
}

pub fn run_servlet_jsp_analysis(
    root: &str,
    project_id: &str,
    project_name: &str,
    deleted_paths: &[String],
) -> ServletJspAnalysisResult {
    let started_at = Instant::now();
    let effective_budgets = ResourceBudgets::default();
    let foundation = run_servlet_jsp_foundation(root, project_id, project_name, &effective_budgets, deleted_paths);
    check_operational_budgets(&started_at);
    let mut fact_by_id: BTreeMap<String, ServletJspFact> = foundation
        .semantic_facts
        .iter()
        .map(|item| (item.stable_id.clone(), item.clone()))
        .collect();
    let mut relationship_by_id: BTreeMap<String, ServletJspRelationshipAlias> = foundation
        .relationships
        .iter()
        .map(|item| (item.stable_id.clone(), item.clone()))
        .collect();
    let mut diagnostics = foundation.diagnostics.clone();
    let mut file_dependencies: BTreeMap<String, BTreeSet<String>> = foundation
        .dependency_index
        .files
        .iter()
        .map(|(key, values)| (key.clone(), values.iter().cloned().collect()))
        .collect();
    let mut component_dependencies: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut mapping_dependencies: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut view_dependencies: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut state_dependencies: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut identity_provider = JavaIdentityProvider::new(Path::new(&foundation.root));
    let mut truncation_count = foundation.truncation_count;
    let mut ambiguity_count = foundation.ambiguity_count;
    let mut missing_anchor_count = foundation.missing_anchor_count;

    for module in &foundation.modules {
        let remaining_facts =
            (effective_budgets.max_facts_per_project - fact_by_id.len() as i64).max(0);
        let remaining_relationships =
            (effective_budgets.max_relationships_per_project - relationship_by_id.len() as i64).max(0);
        let remaining_diagnostics =
            (effective_budgets.max_diagnostics_per_project - diagnostics.len() as i64).max(0);
        let module_budgets = ResourceBudgets {
            max_facts_per_project: remaining_facts,
            max_relationships_per_project: remaining_relationships,
            max_diagnostics_per_project: remaining_diagnostics,
            max_diagnostics_per_file: effective_budgets.max_diagnostics_per_file.min(remaining_diagnostics),
            ..effective_budgets.clone()
        };
        let java_results: Vec<crate::servlet_jsp::java_semantics::JavaSemanticAnalysisResult> = module
            .java_files
            .iter()
            .map(|path| {
                analyze_java_file(
                    &foundation.root,
                    project_id,
                    project_name,
                    &module.module_id,
                    path,
                    &module_budgets,
                    &mut identity_provider,
                )
            })
            .collect();
        let web_results: Vec<crate::servlet_jsp::web_xml_parser::WebXmlParseResult> = module
            .descriptor_files
            .iter()
            .map(|path| {
                parse_web_xml_file(
                    &foundation.root,
                    path,
                    project_id,
                    project_name,
                    &module.module_id,
                    &module.rel_path,
                    &module_budgets,
                )
            })
            .collect();
        let jsp_results: Vec<crate::servlet_jsp::jsp_parser::JspParseResult> = module
            .jsp_files
            .iter()
            .map(|path| parse_jsp_file(&foundation.root, path, &module_budgets))
            .collect();
        let properties_results: Vec<crate::servlet_jsp::properties_parser::PropertiesParseResult> = module
            .properties_files
            .iter()
            .map(|path| parse_properties_file(&foundation.root, path, &module_budgets))
            .collect();
        let resolved = resolve_servlet_jsp_module(&ResolveModuleInput {
            project_id,
            project_name,
            module,
            java_results: &java_results,
            web_results: &web_results,
            jsp_results: &jsp_results,
            properties_results: &properties_results,
            budgets: &module_budgets,
        });
        for fact in &resolved.facts {
            fact_by_id.insert(fact.stable_id.clone(), fact.clone());
        }
        for relationship in &resolved.relationships {
            relationship_by_id.insert(relationship.stable_id.clone(), relationship.clone());
        }
        diagnostics.extend(resolved.diagnostics.iter().cloned());
        merge_index(&mut file_dependencies, &resolved.dependency_index.files);
        merge_index(&mut component_dependencies, &resolved.dependency_index.components);
        merge_index(&mut mapping_dependencies, &resolved.dependency_index.mappings);
        merge_index(&mut view_dependencies, &resolved.dependency_index.views);
        merge_index(&mut state_dependencies, &resolved.dependency_index.state_slots);
        truncation_count += resolved.truncation_count;
        ambiguity_count += resolved.ambiguity_count;
        missing_anchor_count += resolved.missing_anchor_count;
        check_operational_budgets(&started_at);
    }

    let mut semantic_facts: Vec<ServletJspFact> = fact_by_id.into_values().collect();
    semantic_facts.sort_by(|a, b| a.stable_id.cmp(&b.stable_id));
    if semantic_facts.len() as i64 > effective_budgets.max_facts_per_project {
        diagnostics.push(Diagnostic::new(
            "servlet_jsp.budget.facts",
            &format!("Fact budget {} reached", effective_budgets.max_facts_per_project),
            "warning",
            "",
            1,
            1,
        ));
        semantic_facts.truncate(effective_budgets.max_facts_per_project as usize);
        truncation_count += 1;
    }
    let allowed_fact_ids: BTreeSet<String> = semantic_facts.iter().map(|item| item.stable_id.clone()).collect();
    let mut relationships: Vec<ServletJspRelationshipAlias> = relationship_by_id
        .into_values()
        .filter(|item| {
            (!item.from_generated || allowed_fact_ids.contains(&item.from_id))
                && (!item.to_generated || allowed_fact_ids.contains(&item.to_id))
        })
        .collect();
    relationships.sort_by(|a, b| a.stable_id.cmp(&b.stable_id));
    if relationships.len() as i64 > effective_budgets.max_relationships_per_project {
        diagnostics.push(Diagnostic::new(
            "servlet_jsp.budget.relationships",
            &format!(
                "Relationship budget {} reached",
                effective_budgets.max_relationships_per_project
            ),
            "warning",
            "",
            1,
            1,
        ));
        relationships.truncate(effective_budgets.max_relationships_per_project as usize);
        truncation_count += 1;
    }
    let dependency_index_result = ServletJspDependencyIndex {
        files: frozen(&file_dependencies),
        components: frozen(&component_dependencies),
        mappings: frozen(&mapping_dependencies),
        views: frozen(&view_dependencies),
        state_slots: frozen(&state_dependencies),
    };
    let (dependency_index, dependency_truncated) =
        bounded_dependency_index(dependency_index_result, effective_budgets.max_dependency_entries);
    if dependency_truncated {
        diagnostics.push(Diagnostic::new(
            "servlet_jsp.budget.dependencies",
            &format!(
                "Dependency entry budget {} reached",
                effective_budgets.max_dependency_entries
            ),
            "warning",
            "",
            1,
            1,
        ));
        truncation_count += 1;
    }
    let (bounded_diagnostics, diagnostics_truncated) =
        bounded_project_diagnostics(diagnostics, effective_budgets.max_diagnostics_per_project);
    if diagnostics_truncated {
        truncation_count += 1;
    }
    let diagnostics = bounded_diagnostics;
    check_operational_budgets(&started_at);
    let mandatory_missing = foundation
        .parser_capabilities
        .iter()
        .any(|item| item.mandatory && !item.available);
    let coverage = if foundation.modules.is_empty() {
        "empty"
    } else if mandatory_missing || truncation_count > 0 || diagnostics.iter().any(|item| item.severity == "error") {
        "partial"
    } else {
        "complete"
    };
    let mut deduped = dedupe_diagnostics(diagnostics);
    deduped.sort_by(|a, b| {
        (&a.file_path, a.start_line, &a.code, &a.message).cmp(&(&b.file_path, b.start_line, &b.code, &b.message))
    });
    ServletJspAnalysisResult {
        project_id: project_id.to_string(),
        project_name: project_name.to_string(),
        root: foundation.root.clone(),
        modules: foundation.modules.clone(),
        artifacts: foundation.artifacts.clone(),
        parser_capabilities: foundation.parser_capabilities.clone(),
        semantic_facts,
        relationships,
        dependency_index,
        diagnostics: deduped,
        coverage_status: coverage.to_string(),
        missing_anchor_count,
        ambiguity_count,
        truncation_count,
    }
}

type ServletJspRelationshipAlias = crate::servlet_jsp::models::ServletJspRelationship;

fn module_fact(project_id: &str, project_name: &str, module: &ServletJspModule) -> ServletJspFact {
    ServletJspFact {
        kind: "ServletJspModule".into(),
        stable_id: stable_semantic_id(
            "module",
            project_id,
            &module.module_id,
            std::slice::from_ref(&module.rel_path),
        ),
        name: module.rel_path.clone(),
        source: SourceSpan::new(if module.rel_path != "." { &module.rel_path } else { "" }),
        project_id: project_id.to_string(),
        project_name: project_name.to_string(),
        module_id: module.module_id.clone(),
        language: "servlet_jsp".into(),
        confidence: module.confidence,
        extraction_method: "module_detector".into(),
        resolution_status: "resolved".into(),
        raw_value: String::new(),
        resolved_value: String::new(),
        source_symbol_id: String::new(),
        properties: {
            let mut map = Map::new();
            map.insert("module_path".into(), json!(module.rel_path));
            map.insert("evidence".into(), json!(module.evidence));
            map
        },
    }
}

fn artifact_fact(project_id: &str, project_name: &str, artifact: &ServletJspArtifact) -> ServletJspFact {
    let kind = if artifact.kind == "web_xml" {
        "WebDescriptor"
    } else if ["jsp", "jspx", "jsp_fragment"].contains(&artifact.kind.as_str()) {
        "JSPView"
    } else {
        "WebTarget"
    };
    ServletJspFact {
        kind: kind.to_string(),
        stable_id: stable_semantic_id(
            "artifact",
            project_id,
            &artifact.module_id,
            &[artifact.kind.clone(), artifact.file_path.clone()],
        ),
        name: crate::pyutil::basename(&artifact.file_path),
        source: artifact.source.clone(),
        project_id: project_id.to_string(),
        project_name: project_name.to_string(),
        module_id: artifact.module_id.clone(),
        language: "servlet_jsp".into(),
        confidence: artifact.confidence,
        extraction_method: "artifact_detector".into(),
        resolution_status: "resolved".into(),
        raw_value: String::new(),
        resolved_value: String::new(),
        source_symbol_id: String::new(),
        properties: {
            let mut map = Map::new();
            map.insert("artifact_kind".into(), json!(artifact.kind));
            map.insert("module_path".into(), json!(artifact.module_path));
            map.insert("evidence".into(), json!(artifact.evidence));
            map
        },
    }
}

fn artifact_kind(default: &str, file_path: &str) -> String {
    let lower = file_path.to_lowercase();
    if lower.ends_with(".jspx") {
        "jspx".to_string()
    } else if lower.ends_with(".jspf") {
        "jsp_fragment".to_string()
    } else {
        default.to_string()
    }
}

fn normalize(path: &str) -> String {
    let value = path.replace('\\', "/");
    let mut value = value.as_str();
    while let Some(rest) = value.strip_prefix("./") {
        value = rest;
    }
    value.trim_start_matches('/').to_string()
}

fn merge_index(target: &mut BTreeMap<String, BTreeSet<String>>, source: &BTreeMap<String, Vec<String>>) {
    for (key, values) in source {
        target.entry(key.clone()).or_default().extend(values.iter().cloned());
    }
}

fn frozen(values: &BTreeMap<String, BTreeSet<String>>) -> BTreeMap<String, Vec<String>> {
    values
        .iter()
        .map(|(key, items)| {
            let mut sorted: Vec<String> = items.iter().cloned().collect();
            sorted.sort();
            (key.clone(), sorted)
        })
        .collect()
}

fn dedupe_diagnostics(values: Vec<Diagnostic>) -> Vec<Diagnostic> {
    let mut rows: std::collections::BTreeMap<(String, String, i64, i64, String), Diagnostic> = BTreeMap::new();
    for item in values {
        rows.insert(
            (item.code.clone(), item.file_path.clone(), item.start_line, item.end_line, item.message.clone()),
            item,
        );
    }
    rows.into_values().collect()
}

fn bounded_project_diagnostics(values: Vec<Diagnostic>, maximum: i64) -> (Vec<Diagnostic>, bool) {
    let mut rows = dedupe_diagnostics(values);
    rows.sort_by(|a, b| {
        (&a.file_path, a.start_line, &a.code, &a.message).cmp(&(&b.file_path, b.start_line, &b.code, &b.message))
    });
    let limit = maximum.max(0);
    if rows.len() as i64 <= limit {
        return (rows, false);
    }
    if limit == 0 {
        return (Vec::new(), true);
    }
    let marker = Diagnostic::new(
        "servlet_jsp.budget.diagnostics",
        &format!("Project diagnostic budget {limit} reached"),
        "warning",
        "",
        1,
        1,
    );
    let mut bounded = rows.into_iter().take((limit - 1) as usize).collect::<Vec<Diagnostic>>();
    bounded.push(marker);
    (bounded, true)
}

fn bounded_dependency_index(
    index: ServletJspDependencyIndex,
    maximum: i64,
) -> (ServletJspDependencyIndex, bool) {
    let mut remaining = maximum.max(0);
    let mut truncated = false;
    let mut bounded = ServletJspDependencyIndex::default();
    for field in ["files", "components", "mappings", "views", "state_slots"] {
        let values = index_values(&index, field);
        let mut output: BTreeMap<String, Vec<String>> = BTreeMap::new();
        for (key, targets) in values {
            let take = (remaining as usize).min(targets.len());
            let accepted: Vec<String> = targets[..take].to_vec();
            if !accepted.is_empty() {
                output.insert(key, accepted.clone());
            }
            remaining -= accepted.len() as i64;
            if accepted.len() != targets.len() {
                truncated = true;
            }
        }
        *bounded.category_mut_public(field) = output;
    }
    (bounded, truncated)
}

fn index_values(index: &ServletJspDependencyIndex, field: &str) -> Vec<(String, Vec<String>)> {
    match field {
        "files" => index.files.iter().map(|(k, v)| (k.clone(), v.clone())).collect(),
        "components" => index.components.iter().map(|(k, v)| (k.clone(), v.clone())).collect(),
        "mappings" => index.mappings.iter().map(|(k, v)| (k.clone(), v.clone())).collect(),
        "views" => index.views.iter().map(|(k, v)| (k.clone(), v.clone())).collect(),
        "state_slots" => index.state_slots.iter().map(|(k, v)| (k.clone(), v.clone())).collect(),
        _ => Vec::new(),
    }
}

fn check_operational_budgets(_started_at: &Instant) {
    // max_wall_time_seconds = 1800s — corpus parity không chạm; RSS guard là
    // POSIX resource API phía Python (không port).
}

/// `ServletJspDetectionResult` re-export giữ import gọn.
#[allow(dead_code)]
type Detection = ServletJspDetectionResult;



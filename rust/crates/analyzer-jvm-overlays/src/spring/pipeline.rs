//! Port `tools/spring/pipeline.py` — run_spring_foundation.

use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use super::adapters::collect_language_facts;
use super::config::{config_index, parse_config_file};
use super::detector::{SpringProjectDetector, EXCLUDED_DIRS};
use super::extractors::{common, core, crosscutting, messaging, persistence, security};
use super::models::{
    ConfigValue, Diagnostic, LanguageSourceFact, SourceSpan, SpringAnalysisResult, SpringFact, SpringModule,
    SpringRelationship,
};
use super::source_scanner::scan_source_units;
use crate::pyutil::{abs_path, strip_dot_slash, walk_sorted};
use cortex_analyzer_framework::manifest::load_manifest_paths;

pub fn run_spring_foundation(
    root: &Path,
    project_id: &str,
    project_name: &str,
    languages: &[&str],
    incremental: bool,
    changed_files_manifest: &str,
    deleted_files_manifest: &str,
) -> SpringAnalysisResult {
    let root_abs = abs_path(root);
    let mut detector = SpringProjectDetector::new(&root_abs);
    let modules_raw = detector.discover_modules(languages);

    let mut changed_paths: BTreeSet<String> = BTreeSet::new();
    if incremental && !changed_files_manifest.is_empty() {
        changed_paths = load_manifest_paths(changed_files_manifest, &root_abs)
            .into_iter()
            .map(|path| strip_dot_slash(&path))
            .collect();
    }
    let mut deleted_paths: BTreeSet<String> = BTreeSet::new();
    if incremental && !deleted_files_manifest.is_empty() {
        deleted_paths = load_manifest_paths(deleted_files_manifest, &root_abs)
            .into_iter()
            .map(|path| strip_dot_slash(&path))
            .collect();
    }

    let mut module_objects: Vec<SpringModule> = Vec::new();
    let mut config_values: Vec<ConfigValue> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    for item in &modules_raw {
        let module = SpringModule {
            root: root_abs.to_string_lossy().to_string(),
            rel_path: item.rel_path.clone(),
            languages: item.languages.clone(),
            build_files: item.build_files.clone(),
            config_files: item.config_files.clone(),
            evidence: item.evidence.clone(),
            confidence: item.confidence,
        };
        for rel_path in &module.config_files {
            if !changed_paths.is_empty() && !changed_paths.contains(rel_path) {
                continue;
            }
            let (values, config_diags) = parse_config_file(&root_abs, rel_path);
            config_values.extend(values);
            diagnostics.extend(config_diags);
        }
        module_objects.push(module);
    }

    let mut source_paths: BTreeSet<String> = BTreeSet::new();
    for (_abs_path, rel_path_raw) in iter_source_files(&root_abs) {
        let rel_path = strip_dot_slash(&rel_path_raw);
        let detection = detector.detect_path(&rel_path);
        if !detection.is_spring {
            continue;
        }
        if !changed_paths.is_empty() && !changed_paths.contains(&rel_path) {
            continue;
        }
        source_paths.insert(rel_path);
    }
    let source_path_list: Vec<String> = source_paths.into_iter().collect();

    let language_facts: Vec<LanguageSourceFact> =
        collect_language_facts(&root_abs, languages, &source_path_list);

    let source_units = scan_source_units(&root_abs, &source_path_list);
    let mut semantic_facts: Vec<SpringFact> = semantic_facts_from_foundation(
        project_id,
        project_name,
        &module_objects,
        &config_values,
    );
    let mut relationships: Vec<SpringRelationship> = Vec::new();
    let config_index_map = config_index(&config_values);
    let string_index: BTreeMap<String, Vec<String>> = config_index_map
        .iter()
        .map(|(key, value)| {
            let items = match value {
                Value::Array(items) => items.iter().map(crate::pyjson::py_str).collect(),
                other => vec![crate::pyjson::py_str(other)],
            };
            (key.clone(), items)
        })
        .collect();
    for (facts, rels) in [
        core::extract_core_facts(&source_units, project_id, project_name),
        persistence::extract_persistence_facts(&source_units, project_id, project_name),
        messaging::extract_messaging_facts(&source_units, project_id, project_name, &string_index),
        security::extract_security_facts(&source_units, project_id, project_name),
        crosscutting::extract_crosscutting_facts(&source_units, project_id, project_name),
    ] {
        semantic_facts.extend(facts);
        relationships.extend(rels);
    }

    if !deleted_paths.is_empty() {
        diagnostics.push(Diagnostic::new(
            "spring.incremental.deleted_files",
            &format!("{} deleted path(s) require Spring graph cleanup", deleted_paths.len()),
            "info",
            "",
            1,
            1,
        ));
    }

    SpringAnalysisResult {
        project_id: project_id.to_string(),
        project_name: project_name.to_string(),
        root: root_abs.to_string_lossy().to_string(),
        modules: module_objects,
        config_values,
        language_facts,
        semantic_facts: dedupe_facts(semantic_facts),
        relationships: dedupe_relationships(relationships),
        diagnostics,
    }
}

fn semantic_facts_from_foundation(
    project_id: &str,
    project_name: &str,
    modules: &[SpringModule],
    config_values: &[ConfigValue],
) -> Vec<SpringFact> {
    let mut facts: Vec<SpringFact> = Vec::new();
    for module in modules {
        let base = if module.rel_path.is_empty() { "." } else { &module.rel_path };
        let mut stable_module = base.replace('/', ".");
        stable_module = stable_module.trim_matches('.').to_string();
        if stable_module.is_empty() {
            stable_module = "root".to_string();
        }
        let source_file = if !module.build_files.is_empty() {
            module.build_files[0].clone()
        } else if !module.config_files.is_empty() {
            module.config_files[0].clone()
        } else {
            module.rel_path.clone()
        };
        facts.push(SpringFact {
            kind: "SpringModule".to_string(),
            stable_id: format!("spring_module::{project_id}::{stable_module}"),
            name: if module.rel_path.is_empty() {
                ".".to_string()
            } else {
                module.rel_path.clone()
            },
            source: SourceSpan::new(if source_file.is_empty() { "." } else { &source_file }, 1, 1),
            project_id: project_id.to_string(),
            project_name: project_name.to_string(),
            language: "spring".to_string(),
            confidence: module.confidence,
            extraction_method: "spring_foundation".to_string(),
            resolution_status: "resolved".to_string(),
            raw_value: String::new(),
            resolved_value: String::new(),
            source_symbol_id: String::new(),
            properties: serde_json::from_value(json!({
                "module_path": module.rel_path,
                "languages": module.languages,
                "build_files": module.build_files,
                "config_files": module.config_files,
                "evidence": module.evidence,
            }))
            .unwrap_or_default(),
        });
    }
    for config in config_values {
        let key_hash = common::stable_hash(&format!("{}:{}:{}", config.source.file_path, config.profile, config.key));
        facts.push(SpringFact {
            kind: "SpringConfiguration".to_string(),
            stable_id: format!("spring_config_value::{project_id}::{key_hash}"),
            name: config.key.clone(),
            source: config.source.clone(),
            project_id: project_id.to_string(),
            project_name: project_name.to_string(),
            language: "spring".to_string(),
            confidence: 1.0,
            extraction_method: "spring_foundation".to_string(),
            resolution_status: "resolved".to_string(),
            raw_value: config.raw_value.clone(),
            resolved_value: crate::pyjson::py_str(&config.value),
            source_symbol_id: String::new(),
            properties: serde_json::Map::from_iter([
                ("config_key".to_string(), json!(config.key)),
                ("config_value".to_string(), config.value.clone()),
                ("profile".to_string(), json!(config.profile)),
            ]),
        });
    }
    facts
}

fn iter_source_files(root: &Path) -> Vec<(PathBuf, String)> {
    let mut out = Vec::new();
    for (abs_path, rel_path_raw) in walk_sorted(root) {
        let name = abs_path
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        if !(name.ends_with(".java") || name.ends_with(".kt") || name.ends_with(".kts")) {
            continue;
        }
        // Excluded dirs + dot-dirs: lọc theo thành phần của rel path.
        let mut excluded = false;
        for component in rel_path_raw.split('/') {
            if component.starts_with('.') || EXCLUDED_DIRS.contains(&component) {
                excluded = true;
                break;
            }
        }
        if excluded {
            continue;
        }
        let rel_path = strip_dot_slash(&rel_path_raw);
        if !format!("/{rel_path}").contains("/src/") {
            continue;
        }
        out.push((abs_path, rel_path));
    }
    out
}

fn dedupe_facts(facts: Vec<SpringFact>) -> Vec<SpringFact> {
    let mut seen: HashSet<String> = HashSet::new();
    let mut out: Vec<SpringFact> = Vec::new();
    for item in facts {
        if seen.contains(&item.stable_id) {
            continue;
        }
        seen.insert(item.stable_id.clone());
        out.push(item);
    }
    out
}

fn dedupe_relationships(relationships: Vec<SpringRelationship>) -> Vec<SpringRelationship> {
    let mut seen: HashSet<(String, String, String, String, String, i64)> = HashSet::new();
    let mut out: Vec<SpringRelationship> = Vec::new();
    for item in relationships {
        let key = (
            item.rel_type.clone(),
            item.from_id.clone(),
            item.to_id.clone(),
            item.project_id.clone(),
            item.source.file_path.clone(),
            item.source.start_line,
        );
        if seen.contains(&key) {
            continue;
        }
        seen.insert(key);
        out.push(item);
    }
    out
}

/// `cache.py::write_fact_artifact` — JSON artifact (payload = to_dict() của
/// result; sort_keys + indent 2 + ensure_ascii + newline).
pub fn result_to_dict(result: &SpringAnalysisResult) -> Value {
    crate::pyjson::py_object(vec![
        ("project_id".into(), json!(result.project_id)),
        ("project_name".into(), json!(result.project_name)),
        ("root".into(), json!(result.root)),
        (
            "modules".into(),
            Value::Array(
                result
                    .modules
                    .iter()
                    .map(|module| {
                        crate::pyjson::py_object(vec![
                            ("root".into(), json!(module.root)),
                            ("rel_path".into(), json!(module.rel_path)),
                            ("languages".into(), json!(module.languages)),
                            ("build_files".into(), json!(module.build_files)),
                            ("config_files".into(), json!(module.config_files)),
                            ("evidence".into(), json!(module.evidence)),
                            ("confidence".into(), json!(module.confidence)),
                        ])
                    })
                    .collect(),
            ),
        ),
        (
            "config_values".into(),
            Value::Array(
                result
                    .config_values
                    .iter()
                    .map(|config| {
                        crate::pyjson::py_object(vec![
                            ("key".into(), json!(config.key)),
                            ("value".into(), config.value.clone()),
                            (
                                "source".into(),
                                span_dict(&config.source),
                            ),
                            ("profile".into(), json!(config.profile)),
                            ("raw_value".into(), json!(config.raw_value)),
                            ("resolution_status".into(), json!(config.resolution_status)),
                        ])
                    })
                    .collect(),
            ),
        ),
        (
            "language_facts".into(),
            Value::Array(
                result
                    .language_facts
                    .iter()
                    .map(|item| {
                        crate::pyjson::py_object(vec![
                            ("language".into(), json!(item.language)),
                            ("file_path".into(), json!(item.file_path)),
                            ("source_symbol_id".into(), json!(item.source_symbol_id)),
                            ("package_name".into(), json!(item.package_name)),
                            ("declarations".into(), json!(item.declarations)),
                            ("annotations".into(), json!(item.annotations)),
                            ("parser_status".into(), json!(item.parser_status)),
                        ])
                    })
                    .collect(),
            ),
        ),
        (
            "semantic_facts".into(),
            Value::Array(result.semantic_facts.iter().map(fact_to_dict).collect()),
        ),
        (
            "relationships".into(),
            Value::Array(result.relationships.iter().map(rel_to_dict).collect()),
        ),
        (
            "diagnostics".into(),
            Value::Array(
                result
                    .diagnostics
                    .iter()
                    .map(|item| {
                        crate::pyjson::py_object(vec![
                            ("code".into(), json!(item.code)),
                            ("message".into(), json!(item.message)),
                            ("severity".into(), json!(item.severity)),
                            ("file_path".into(), json!(item.file_path)),
                            ("start_line".into(), json!(item.start_line)),
                            ("end_line".into(), json!(item.end_line)),
                        ])
                    })
                    .collect(),
            ),
        ),
        ("parser_version".into(), json!(super::models::SPRING_PARSER_VERSION)),
    ])
}

fn span_dict(span: &SourceSpan) -> Value {
    crate::pyjson::py_object(vec![
        ("file_path".into(), json!(span.file_path)),
        ("start_line".into(), json!(span.start_line)),
        ("end_line".into(), json!(span.end_line)),
    ])
}

fn fact_to_dict(fact: &SpringFact) -> Value {
    let properties = crate::pyjson::py_object(
        fact.properties
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
    );
    crate::pyjson::py_object(vec![
        ("kind".into(), json!(fact.kind)),
        ("stable_id".into(), json!(fact.stable_id)),
        ("name".into(), json!(fact.name)),
        ("source".into(), span_dict(&fact.source)),
        ("project_id".into(), json!(fact.project_id)),
        ("project_name".into(), json!(fact.project_name)),
        ("language".into(), json!(fact.language)),
        ("confidence".into(), json!(fact.confidence)),
        ("extraction_method".into(), json!(fact.extraction_method)),
        ("resolution_status".into(), json!(fact.resolution_status)),
        ("raw_value".into(), json!(fact.raw_value)),
        ("resolved_value".into(), json!(fact.resolved_value)),
        ("source_symbol_id".into(), json!(fact.source_symbol_id)),
        ("properties".into(), properties),
    ])
}

fn rel_to_dict(item: &SpringRelationship) -> Value {
    let properties = crate::pyjson::py_object(
        item.properties
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
    );
    crate::pyjson::py_object(vec![
        ("type".into(), json!(item.rel_type)),
        ("from_label".into(), json!(item.from_label)),
        ("from_id".into(), json!(item.from_id)),
        ("to_label".into(), json!(item.to_label)),
        ("to_id".into(), json!(item.to_id)),
        ("project_id".into(), json!(item.project_id)),
        ("confidence".into(), json!(item.confidence)),
        ("resolution_status".into(), json!(item.resolution_status)),
        ("reason".into(), json!(item.reason)),
        ("source".into(), span_dict(&item.source)),
        ("properties".into(), properties),
    ])
}

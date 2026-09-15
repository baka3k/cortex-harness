//! Port `tools/project_topology/resolver.py` — deterministic module/dependency
//! resolution across descriptor formats.

use std::collections::{BTreeMap, BTreeSet};

use crate::models::{
    confidence, deterministic_unique, diagnostic_code, module_kind, normalize_module_path,
    sorted_unique, stable_fact_id, stable_module_id, AnalysisDiagnostic, DependencyFact,
    DescriptorFact, FrameworkInstanceFact, ModuleFact, PyValue, SourceEvidence, SpecialFileFact,
};

fn _kind(value: &str) -> &'static str {
    match value {
        "root" => module_kind::ROOT,
        "android_application" => module_kind::ANDROID_APPLICATION,
        "android_dynamic_feature" => module_kind::ANDROID_DYNAMIC_FEATURE,
        "android_library" => module_kind::ANDROID_LIBRARY,
        "jvm_application" => module_kind::JVM_APPLICATION,
        "jvm_library" => module_kind::JVM_LIBRARY,
        "maven_module" => module_kind::MAVEN_MODULE,
        "native_executable" => module_kind::NATIVE_EXECUTABLE,
        "native_library" => module_kind::NATIVE_LIBRARY,
        "package" => module_kind::PACKAGE,
        "database" => module_kind::DATABASE,
        "test" => module_kind::TEST,
        "unknown" => module_kind::UNKNOWN,
        _ => module_kind::UNKNOWN,
    }
}

fn kind_priority(kind: &str) -> i64 {
    match kind {
        k if k == module_kind::ANDROID_APPLICATION => 100,
        k if k == module_kind::ANDROID_DYNAMIC_FEATURE => 95,
        k if k == module_kind::ANDROID_LIBRARY => 90,
        k if k == module_kind::JVM_APPLICATION => 80,
        k if k == module_kind::JVM_LIBRARY => 75,
        k if k == module_kind::NATIVE_EXECUTABLE => 70,
        k if k == module_kind::NATIVE_LIBRARY => 65,
        k if k == module_kind::MAVEN_MODULE => 60,
        k if k == module_kind::PACKAGE => 50,
        k if k == module_kind::DATABASE => 40,
        k if k == module_kind::ROOT => 10,
        _ => 0,
    }
}

const FRAMEWORK_MARKERS: &[(&str, &[&str])] = &[
    ("spring", &["spring", "springframework"]),
    ("mybatis", &["mybatis"]),
    ("servlet_jsp", &["servlet", "jakarta.servlet", "javax.servlet"]),
    ("struts", &["struts"]),
    ("flutter", &["flutter"]),
    ("aspnet_core", &["microsoft.aspnetcore"]),
    ("aspnet_framework", &["system.web"]),
    ("fastapi_django", &["fastapi", "django"]),
    ("express_js", &["express"]),
    ("laravel", &["laravel", "illuminate"]),
    ("database_sql", &["dbt", "flyway", "liquibase"]),
    ("database_plsql", &["oracle", "plsql"]),
];

fn declared_module_paths(descriptor: &DescriptorFact) -> Vec<String> {
    let Some(declared) = descriptor.properties.get("declared_modules") else {
        return Vec::new();
    };
    match declared.as_list() {
        Some(items) => items
            .iter()
            .filter_map(|value| match value {
                PyValue::Str(text) => Some(text.clone()),
                _ => None,
            })
            .map(|value| normalize_module_path(&value).unwrap_or_else(|_| ".".to_string()))
            .collect(),
        None => Vec::new(),
    }
}

fn detect_cycles(dependencies: &[DependencyFact]) -> Vec<Vec<String>> {
    let mut graph: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for item in dependencies {
        if item.internal && item.target_module_path.is_some() {
            graph.entry(item.source_module_path.clone()).or_default().insert(
                item.target_module_path
                    .clone()
                    .unwrap_or_default(),
            );
        }
    }
    let mut cycles: BTreeSet<Vec<String>> = BTreeSet::new();

    fn visit(
        node: &str,
        graph: &BTreeMap<String, BTreeSet<String>>,
        stack: &mut Vec<String>,
        active: &mut BTreeSet<String>,
        seen: &mut BTreeSet<String>,
        cycles: &mut BTreeSet<Vec<String>>,
    ) {
        if active.contains(node) {
            let start = stack.iter().position(|item| item == node).unwrap_or(0);
            let mut cycle: Vec<String> = stack[start..].to_vec();
            cycle.push(node.to_string());
            // rotations như Python — min lexicographic.
            let mut rotations: Vec<Vec<String>> = Vec::new();
            for index in 0..cycle.len() - 1 {
                let mut rotation: Vec<String> = cycle[index..cycle.len() - 1].to_vec();
                rotation.extend(cycle[..index].iter().cloned());
                rotation.push(cycle[index].clone());
                rotations.push(rotation);
            }
            rotations.sort();
            if let Some(min_rotation) = rotations.into_iter().next() {
                cycles.insert(min_rotation);
            }
            return;
        }
        if seen.contains(node) {
            return;
        }
        active.insert(node.to_string());
        stack.push(node.to_string());
        if let Some(targets) = graph.get(node) {
            for target in targets {
                visit(target, graph, stack, active, seen, cycles);
            }
        }
        stack.pop();
        active.remove(node);
        seen.insert(node.to_string());
    }

    let mut seen = BTreeSet::new();
    for node in graph.keys() {
        visit(
            node,
            &graph,
            &mut Vec::new(),
            &mut BTreeSet::new(),
            &mut seen,
            &mut cycles,
        );
    }
    cycles.into_iter().collect()
}

pub struct ResolvedTopology {
    pub modules: Vec<ModuleFact>,
    pub dependencies: Vec<DependencyFact>,
    pub special_files: Vec<SpecialFileFact>,
    pub frameworks: Vec<FrameworkInstanceFact>,
    pub diagnostics: Vec<AnalysisDiagnostic>,
}

pub fn resolve_topology(
    project_id: &str,
    descriptors: &[DescriptorFact],
    dependencies: &[DependencyFact],
) -> Result<ResolvedTopology, String> {
    let mut paths: BTreeSet<String> = descriptors
        .iter()
        .map(|item| item.module_path.clone())
        .collect();
    for descriptor in descriptors {
        for value in declared_module_paths(descriptor) {
            paths.insert(value);
        }
    }
    if paths.is_empty() {
        paths.insert(".".to_string());
    }
    let mut by_path: BTreeMap<String, Vec<&DescriptorFact>> = BTreeMap::new();
    for path in &paths {
        by_path.insert(path.clone(), Vec::new());
    }
    for descriptor in descriptors {
        by_path
            .entry(descriptor.module_path.clone())
            .or_default()
            .push(descriptor);
    }

    // coordinate_to_path — dict comprehension theo descriptor order, trùng key
    // ghi đè.
    let mut coordinate_to_path: Vec<(String, String)> = Vec::new();
    for descriptor in descriptors {
        if let Some(coordinate) = descriptor.properties.get("coordinate") {
            if !coordinate.truthy() {
                continue;
            }
            let key = coordinate.as_str().to_string();
            if let Some(entry) = coordinate_to_path.iter_mut().find(|(k, _)| *k == key) {
                entry.1 = descriptor.module_path.clone();
            } else {
                coordinate_to_path.push((key, descriptor.module_path.clone()));
            }
        }
    }

    let mut resolved_dependencies: Vec<DependencyFact> = Vec::new();
    for dependency in dependencies {
        let target_path = dependency.target_module_path.clone();
        if let Some(target) = target_path {
            let normalized = normalize_module_path(&target)?;
            if paths.contains(&normalized) {
                let mut resolved = dependency.clone();
                resolved.internal = true;
                resolved.target_module_path = Some(normalized);
                resolved_dependencies.push(resolved);
                continue;
            }
        }
        let target_coordinate: String = dependency
            .target
            .split(':')
            .take(2)
            .collect::<Vec<&str>>()
            .join(":");
        let coordinate_match = coordinate_to_path
            .iter()
            .find(|(coordinate, _)| {
                coordinate
                    .split(':')
                    .take(2)
                    .collect::<Vec<&str>>()
                    .join(":")
                    == target_coordinate
            })
            .map(|(_, module_path)| module_path.clone());
        if let Some(matched) = coordinate_match {
            let mut resolved = dependency.clone();
            resolved.internal = true;
            resolved.target_module_path = Some(matched);
            resolved_dependencies.push(resolved);
        } else {
            resolved_dependencies.push(dependency.clone());
        }
    }

    let mut diagnostics: Vec<AnalysisDiagnostic> = Vec::new();
    let mut modules: Vec<ModuleFact> = Vec::new();
    let mut frameworks: Vec<FrameworkInstanceFact> = Vec::new();
    for module_path in &paths {
        let mut facts: Vec<&DescriptorFact> =
            by_path.get(module_path).cloned().unwrap_or_default();
        facts.sort_by(|a, b| a.path.cmp(&b.path));
        let kinds: Vec<&'static str> = facts
            .iter()
            .map(|item| {
                _kind(
                    item.properties
                        .get("module_kind")
                        .map(PyValue::as_str)
                        .unwrap_or("unknown"),
                )
            })
            .collect();
        let concrete_kinds: BTreeSet<&'static str> =
            kinds.iter().copied().filter(|kind| *kind != module_kind::UNKNOWN).collect();
        let default_kind = if module_path == "." {
            module_kind::ROOT
        } else {
            module_kind::UNKNOWN
        };
        let selected_kind = concrete_kinds
            .iter()
            .copied()
            .max_by_key(|kind| kind_priority(kind))
            .unwrap_or(default_kind);
        let mut module_diagnostics: Vec<AnalysisDiagnostic> = Vec::new();
        if concrete_kinds.len() > 1 {
            let diagnostic = AnalysisDiagnostic::new(
                diagnostic_code::AMBIGUOUS_MODULE_KIND,
                "Conflicting descriptor evidence produced more than one module kind.",
            )
            .file_path(facts.first().map(|item| item.path.as_str()).unwrap_or(""))
            .module_path(module_path)
            .details({
                let mut details = PyValue::dict();
                details.set(
                    "kinds",
                    PyValue::List(
                        sorted_unique(concrete_kinds.iter().copied().map(str::to_string))
                            .into_iter()
                            .map(PyValue::Str)
                            .collect(),
                    ),
                );
                details
            });
            module_diagnostics.push(diagnostic.clone());
            diagnostics.push(diagnostic);
        }
        let build_systems = deterministic_unique(facts.iter().map(|item| {
            item.properties
                .get("build_system")
                .map(PyValue::as_str)
                .unwrap_or("")
                .to_string()
        }));
        let names: Vec<String> = facts
            .iter()
            .map(|item| {
                for key in ["root_name", "artifact_id", "name"] {
                    if let Some(value) = item.properties.get(key)
                        && value.truthy()
                    {
                        return value.as_str().to_string();
                    }
                }
                String::new()
            })
            .collect();
        let mut languages: Vec<String> = Vec::new();
        if selected_kind.starts_with("android")
            || selected_kind.starts_with("jvm")
            || selected_kind == module_kind::MAVEN_MODULE
        {
            languages.extend(["java".to_string(), "kotlin".to_string()]);
        } else if selected_kind.starts_with("native") {
            languages.extend(["c".to_string(), "cplus".to_string()]);
        }
        // marker_text — " ".join([...]) sau .lower(); str(properties) là repr
        // Python dict (insertion order).
        let mut marker_parts: Vec<String> = Vec::new();
        for item in &facts {
            marker_parts.push(item.parser.clone());
        }
        for item in &facts {
            marker_parts.push(item.path.clone());
        }
        for item in &facts {
            marker_parts.push(item.properties.repr());
        }
        for item in &resolved_dependencies {
            if item.source_module_path == *module_path {
                marker_parts.push(item.target.clone());
            }
        }
        let marker_text = marker_parts.join(" ").to_lowercase();
        let detected_frameworks: Vec<String> = FRAMEWORK_MARKERS
            .iter()
            .filter(|(_, markers)| {
                markers
                    .iter()
                    .any(|marker| marker_text.contains(marker))
            })
            .map(|(framework, _)| framework.to_string())
            .collect();
        let detected_frameworks = deterministic_unique(detected_frameworks);
        let module_id = stable_module_id(project_id, module_path)?;
        // ModuleFact.create: name mặc định = "root" cho "." hoặc basename
        // của module path (pathlib Path(normalized).name).
        let resolved_name = names
            .into_iter()
            .find(|name| !name.is_empty())
            .unwrap_or_else(|| {
                if module_path == "." {
                    "root".to_string()
                } else {
                    module_path
                        .rsplit('/')
                        .next()
                        .unwrap_or(module_path)
                        .to_string()
                }
            });
        let descriptor_ids: Vec<String> = facts.iter().map(|item| item.id.clone()).collect();
        let confidence_value = if !module_diagnostics.is_empty()
            || facts.iter().any(|item| !item.diagnostics.is_empty())
        {
            confidence::MEDIUM
        } else {
            confidence::HIGH
        };
        modules.push(ModuleFact {
            id: module_id.clone(),
            project_id: project_id.to_string(),
            module_path: module_path.clone(),
            name: resolved_name,
            kind: selected_kind,
            languages: deterministic_unique(languages),
            frameworks: detected_frameworks.clone(),
            build_systems,
            source_roots: Vec::new(),
            descriptor_ids,
            confidence: confidence_value,
            diagnostics: module_diagnostics,
            properties: PyValue::dict(),
        });
        for framework in &detected_frameworks {
            let framework_id =
                stable_fact_id(project_id, "framework-instance", &[&module_id, framework])?;
            let evidence_facts: Vec<SourceEvidence> = facts
                .iter()
                .filter(|item| {
                    let properties_text = item.properties.repr().to_lowercase();
                    let parser_text = item.parser.to_lowercase();
                    properties_text.contains(&framework.to_lowercase())
                        || parser_text.contains(&framework.to_lowercase())
                })
                .map(|item| SourceEvidence::new(&item.path))
                .take(10)
                .collect();
            let mut dimensions = PyValue::dict();
            dimensions.set("configuration", PyValue::Str("partial".to_string()));
            dimensions.set("endpoints", PyValue::Str("partial".to_string()));
            dimensions.set("security", PyValue::Str("unknown".to_string()));
            dimensions.set("persistence", PyValue::Str("partial".to_string()));
            dimensions.set("messaging_jobs", PyValue::Str("unknown".to_string()));
            dimensions.set("ui_resources", PyValue::Str("partial".to_string()));
            dimensions.set("deployment", PyValue::Str("unknown".to_string()));
            frameworks.push(FrameworkInstanceFact {
                id: framework_id,
                project_id: project_id.to_string(),
                module_id: module_id.clone(),
                framework: framework.clone(),
                version: String::new(),
                confidence: confidence::MEDIUM,
                evidence: evidence_facts,
                dimensions,
                facts: PyValue::dict(),
                diagnostics: Vec::new(),
            });
        }
    }

    let module_ids: BTreeMap<String, String> = modules
        .iter()
        .map(|item| (item.module_path.clone(), item.id.clone()))
        .collect();
    let mut sorted_descriptors: Vec<&DescriptorFact> = descriptors.iter().collect();
    sorted_descriptors.sort_by(|a, b| a.path.cmp(&b.path));
    let special_files: Vec<SpecialFileFact> = sorted_descriptors
        .iter()
        .map(|descriptor| {
            let module_id = module_ids
                .get(&descriptor.module_path)
                .cloned()
                .unwrap_or_default();
            SpecialFileFact {
                descriptor_id: descriptor.id.clone(),
                project_id: project_id.to_string(),
                module_id,
                path: descriptor.path.clone(),
                role: descriptor.role,
                parser: descriptor.parser.clone(),
                parse_depth: descriptor.parse_depth,
                status: "present".to_string(),
                framework: String::new(),
                canonical: descriptor.canonical,
                generated: descriptor.generated,
                secret_bearing: descriptor.secret_bearing,
                redacted: descriptor.redacted,
                safe_summary: descriptor.summary.clone(),
                freshness: "current".to_string(),
                diagnostics: descriptor.diagnostics.clone(),
            }
        })
        .collect();
    for cycle in detect_cycles(&resolved_dependencies) {
        diagnostics.push(
            AnalysisDiagnostic::new(
                diagnostic_code::UNRESOLVED_REFERENCE,
                "Internal module dependency cycle detected.",
            )
            .module_path(&cycle[0])
            .details({
                let mut details = PyValue::dict();
                details.set(
                    "cycle",
                    PyValue::List(cycle.iter().cloned().map(PyValue::Str).collect()),
                );
                details
            }),
        );
    }
    let mut sorted_dependencies = resolved_dependencies;
    sorted_dependencies.sort_by(|a, b| a.id.cmp(&b.id));
    let mut sorted_frameworks = frameworks;
    sorted_frameworks.sort_by(|a, b| a.id.cmp(&b.id));
    Ok(ResolvedTopology {
        modules,
        dependencies: sorted_dependencies,
        special_files,
        frameworks: sorted_frameworks,
        diagnostics,
    })
}

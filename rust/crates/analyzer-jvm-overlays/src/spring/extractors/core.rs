//! Port `tools/spring/extractors/core.py`.

use serde_json::{json, Value};

use super::common::{
    annotation_map, bean_name, class_owner_id, fact, first_annotation, has_annotation, method_owner_id, rel,
    stable_hash, FactProps,
};
use crate::spring::models::{SpringFact, SpringRelationship};
use crate::spring::source_scanner::{SourceClass, SourceUnit};
use crate::spring::annotation_catalog::{
    application_annotations, bean_annotations, component_annotations, configuration_annotations,
    controller_annotations, http_mapping_annotations, http_mapping_methods, injection_annotations,
    repository_annotations, service_annotations, value_annotations,
};
use crate::spring::value_resolver::ValueResolver;

pub fn extract_core_facts(
    units: &[SourceUnit],
    project_id: &str,
    project_name: &str,
) -> (Vec<SpringFact>, Vec<SpringRelationship>) {
    let resolver = ValueResolver::new();
    let mut facts: Vec<SpringFact> = Vec::new();
    let mut relationships: Vec<SpringRelationship> = Vec::new();
    for unit in units {
        for cls in &unit.classes {
            let class_anns = annotation_map(&cls.annotations);
            let mut class_nodes: Vec<(String, String)> = Vec::new();

            let app_ann = first_annotation(&cls.annotations, &application_annotations());
            if app_ann.is_some() || cls.code.contains("SpringApplication.run") {
                let app_id = format!("spring_app::{project_id}::{}", cls.qualified_name());
                let empty = std::collections::BTreeMap::new();
                let app_args = app_ann.map(|a| &a.args).unwrap_or(&empty);
                facts.push(fact(
                    "SpringApplication",
                    &app_id,
                    &cls.name,
                    &cls.source,
                    project_id,
                    project_name,
                    &cls.language,
                    1.0,
                    "resolved",
                    "",
                    "",
                    &class_owner_id(cls),
                    FactProps(vec![
                        ("application_class".into(), json!(cls.qualified_name())),
                        (
                            "scan_packages".into(),
                            json!(resolver.list_arg(app_args, &["scanBasePackages", "scanBasePackageClasses"])),
                        ),
                        ("exclusions".into(), json!(resolver.list_arg(app_args, &["exclude", "excludeName"]))),
                    ]),
                ));
                class_nodes.push(("SpringApplication".into(), app_id));
            }

            if let Some(config_ann) = first_annotation(&cls.annotations, &configuration_annotations()) {
                let config_id = format!("spring_config::{project_id}::{}", stable_hash(&class_owner_id(cls)));
                facts.push(fact(
                    "SpringConfiguration",
                    &config_id,
                    &cls.name,
                    &cls.source,
                    project_id,
                    project_name,
                    &cls.language,
                    1.0,
                    "resolved",
                    "",
                    "",
                    &class_owner_id(cls),
                    FactProps(vec![
                        (
                            "proxy_bean_methods".into(),
                            resolver.first_arg(&config_ann.args, &["proxyBeanMethods"], ""),
                        ),
                        ("profiles".into(), json!(annotation_values(&class_anns, "Profile"))),
                        ("conditions".into(), json!(condition_annotations(&cls.annotations))),
                    ]),
                ));
                class_nodes.push(("SpringConfiguration".into(), config_id));
            }

            let stereotype = first_annotation(&cls.annotations, &component_annotations());
            let mut bean_id = String::new();
            if let Some(stereotype) = stereotype {
                let bean = bean_name(&cls.name, Some(stereotype));
                bean_id = format!(
                    "spring_bean::{project_id}::{}::{}",
                    stable_hash(&class_owner_id(cls)),
                    bean
                );
                facts.push(fact(
                    "SpringBean",
                    &bean_id,
                    &bean,
                    &cls.source,
                    project_id,
                    project_name,
                    &cls.language,
                    1.0,
                    "resolved",
                    "",
                    "",
                    &class_owner_id(cls),
                    FactProps(vec![
                        ("bean_type".into(), json!(cls.qualified_name())),
                        ("stereotype".into(), json!(stereotype.short_name())),
                        ("qualifiers".into(), json!(annotation_values(&class_anns, "Qualifier"))),
                        ("primary".into(), json!(class_anns.contains_key("Primary"))),
                        ("profiles".into(), json!(annotation_values(&class_anns, "Profile"))),
                    ]),
                ));
                class_nodes.push(("SpringBean".into(), bean_id.clone()));
            }

            if has_annotation(&cls.annotations, &controller_annotations()) {
                let controller_id = format!("spring_controller::{project_id}::{}", stable_hash(&class_owner_id(cls)));
                let controller_type = first_annotation(&cls.annotations, &controller_annotations())
                    .map(|a| a.short_name())
                    .unwrap_or_default();
                facts.push(fact(
                    "Controller",
                    &controller_id,
                    &cls.name,
                    &cls.source,
                    project_id,
                    project_name,
                    &cls.language,
                    1.0,
                    "resolved",
                    "",
                    "",
                    &class_owner_id(cls),
                    FactProps(vec![
                        ("controller_class".into(), json!(cls.qualified_name())),
                        ("controller_type".into(), Value::String(controller_type)),
                    ]),
                ));
                class_nodes.push(("Controller".into(), controller_id));
            }

            if has_annotation(&cls.annotations, &service_annotations()) {
                let service_id = format!("spring_service::{project_id}::{}", stable_hash(&class_owner_id(cls)));
                facts.push(fact(
                    "Service",
                    &service_id,
                    &cls.name,
                    &cls.source,
                    project_id,
                    project_name,
                    &cls.language,
                    1.0,
                    "resolved",
                    "",
                    "",
                    &class_owner_id(cls),
                    FactProps(vec![("service_class".into(), json!(cls.qualified_name()))]),
                ));
                class_nodes.push(("Service".into(), service_id));
            }

            if has_annotation(&cls.annotations, &repository_annotations()) {
                let repo_id = format!("spring_repo::{project_id}::{}", stable_hash(&class_owner_id(cls)));
                facts.push(fact(
                    "DataRepository",
                    &repo_id,
                    &cls.name,
                    &cls.source,
                    project_id,
                    project_name,
                    &cls.language,
                    1.0,
                    "resolved",
                    "",
                    "",
                    &class_owner_id(cls),
                    FactProps(vec![
                        ("repository_class".into(), json!(cls.qualified_name())),
                        ("repository_kind".into(), json!("spring_repository_stereotype")),
                    ]),
                ));
                class_nodes.push(("DataRepository".into(), repo_id));
            }

            for (node_label, node_id) in &class_nodes {
                relationships.push(rel(
                    "SEMANTIC_OF",
                    node_label,
                    node_id,
                    "Class",
                    &class_owner_id(cls),
                    project_id,
                    &cls.source,
                    "Spring semantic class anchor",
                    1.0,
                    "resolved",
                    FactProps(vec![]),
                ));
            }

            let (method_facts, method_rels) =
                extract_methods(cls, project_id, project_name, &bean_id, &resolver);
            facts.extend(method_facts);
            relationships.extend(method_rels);
        }
    }
    (facts, relationships)
}

fn extract_methods(
    cls: &SourceClass,
    project_id: &str,
    project_name: &str,
    bean_id: &str,
    resolver: &ValueResolver,
) -> (Vec<SpringFact>, Vec<SpringRelationship>) {
    let mut facts: Vec<SpringFact> = Vec::new();
    let mut relationships: Vec<SpringRelationship> = Vec::new();
    let class_paths = mapping_paths(&cls.annotations, resolver);
    let controller_id = format!("spring_controller::{project_id}::{}", stable_hash(&class_owner_id(cls)));
    for method in &cls.methods {
        let anns = annotation_map(&method.annotations);
        if let Some(bean_ann) = first_annotation(&method.annotations, &bean_annotations()) {
            let produced_name = bean_name(&method.name, Some(bean_ann));
            let produced_id = format!(
                "spring_bean::{project_id}::{}::{}",
                stable_hash(&method_owner_id(method)),
                produced_name
            );
            facts.push(fact(
                "SpringBean",
                &produced_id,
                &produced_name,
                &method.source,
                project_id,
                project_name,
                &method.language,
                1.0,
                "resolved",
                "",
                "",
                &method_owner_id(method),
                FactProps(vec![
                    ("bean_type".into(), json!(method.return_type)),
                    ("origin".into(), json!("bean_method")),
                    ("qualifiers".into(), json!(annotation_values(&anns, "Qualifier"))),
                    ("primary".into(), json!(anns.contains_key("Primary"))),
                    ("scope".into(), Value::String(first_annotation_value(&anns, "Scope"))),
                ]),
            ));
            relationships.push(rel(
                "SEMANTIC_OF",
                "SpringBean",
                &produced_id,
                "Function",
                &method_owner_id(method),
                project_id,
                &method.source,
                "Bean method anchor",
                1.0,
                "resolved",
                FactProps(vec![]),
            ));
            if !bean_id.is_empty() {
                relationships.push(rel(
                    "PRODUCES_BEAN",
                    "SpringBean",
                    bean_id,
                    "SpringBean",
                    &produced_id,
                    project_id,
                    &method.source,
                    "@Bean factory method",
                    1.0,
                    "resolved",
                    FactProps(vec![]),
                ));
            }
        }

        if has_annotation(&method.annotations, &injection_annotations()) && !bean_id.is_empty() {
            let site_id = format!("spring_injection::{project_id}::{}", stable_hash(&method_owner_id(method)));
            facts.push(fact(
                "SpringBean",
                &site_id,
                &format!("{}.{}", cls.name, method.name),
                &method.source,
                project_id,
                project_name,
                &method.language,
                0.72,
                "unresolved",
                &method.params,
                "",
                &method_owner_id(method),
                FactProps(vec![("injection_kind".into(), json!("method"))]),
            ));
            relationships.push(rel(
                "POSSIBLE_INJECTION",
                "SpringBean",
                bean_id,
                "SpringBean",
                &site_id,
                project_id,
                &method.source,
                "Annotated injection site",
                0.72,
                "unresolved",
                FactProps(vec![]),
            ));
        }

        for value_ann in method
            .annotations
            .iter()
            .filter(|ann| value_annotations().contains(ann.short_name().as_str()))
        {
            let value_id = format!(
                "spring_config_binding::{project_id}::{}",
                stable_hash(&format!("{}{}", method_owner_id(method), value_ann.raw))
            );
            // `args.get("value") or args.get("prefix") or ""`
            let binding_value = truthy_or(value_ann.args.get("value"), value_ann.args.get("prefix"));
            facts.push(fact(
                "SpringConfiguration",
                &value_id,
                &format!("{}.{}", cls.name, method.name),
                &method.source,
                project_id,
                project_name,
                &method.language,
                1.0,
                "resolved",
                "",
                "",
                &method_owner_id(method),
                FactProps(vec![
                    ("raw_value".into(), json!(value_ann.raw_args)),
                    ("binding_kind".into(), Value::String(value_ann.short_name())),
                    ("binding_value".into(), binding_value),
                ]),
            ));
        }

        let method_mapping = first_annotation(&method.annotations, &http_mapping_annotations());
        if let Some(method_mapping) = method_mapping {
            let method_paths = mapping_paths(&method.annotations, resolver);
            let http_methods = mapping_methods(method_mapping);
            for path in resolver.combine_paths(&class_paths, &method_paths) {
                for http_method in &http_methods {
                    let endpoint_id = format!(
                        "spring_endpoint::{project_id}::{}::{}::{}",
                        stable_hash(&method_owner_id(method)),
                        http_method,
                        stable_hash(&path)
                    );
                    facts.push(fact(
                        "ApiEndpoint",
                        &endpoint_id,
                        &format!("{http_method} {path}"),
                        &method.source,
                        project_id,
                        project_name,
                        &method.language,
                        1.0,
                        "resolved",
                        "",
                        "",
                        &method_owner_id(method),
                        FactProps(vec![
                            ("path".into(), json!(path)),
                            ("http_method".into(), json!(http_method)),
                            ("handler_names".into(), json!([method.name])),
                            ("controller_class".into(), json!(cls.qualified_name())),
                            ("framework".into(), json!("spring")),
                        ]),
                    ));
                    relationships.push(rel(
                        "SEMANTIC_OF",
                        "ApiEndpoint",
                        &endpoint_id,
                        "Function",
                        &method_owner_id(method),
                        project_id,
                        &method.source,
                        "Endpoint handler anchor",
                        1.0,
                        "resolved",
                        FactProps(vec![]),
                    ));
                    relationships.push(rel(
                        "HANDLES",
                        "Controller",
                        &controller_id,
                        "ApiEndpoint",
                        &endpoint_id,
                        project_id,
                        &method.source,
                        "Controller handles endpoint",
                        1.0,
                        "resolved",
                        FactProps(vec![]),
                    ));
                }
            }
        }
    }
    (facts, relationships)
}

fn truthy_or(first: Option<&Value>, second: Option<&Value>) -> Value {
    if let Some(value) = first.filter(|value| crate::pyjson::py_truthy(value)) {
        return value.clone();
    }
    if let Some(value) = second.filter(|value| crate::pyjson::py_truthy(value)) {
        return value.clone();
    }
    json!("")
}

fn mapping_paths(annotations: &[crate::spring::source_scanner::SourceAnnotation], resolver: &ValueResolver) -> Vec<String> {
    let mut paths: Vec<String> = Vec::new();
    for ann in annotations {
        if !http_mapping_annotations().contains(ann.short_name().as_str()) {
            continue;
        }
        paths.extend(list_arg_for(&ann.args, &["value", "path"], resolver));
    }
    if paths.is_empty() {
        vec![String::new()]
    } else {
        paths.iter().map(|path| resolver.normalize_path(path)).collect()
    }
}

/// `list_arg(ann.args, "value", "path")` qua ValueResolver.
fn list_arg_for(args: &std::collections::BTreeMap<String, Value>, names: &[&str], resolver: &ValueResolver) -> Vec<String> {
    resolver.list_arg(args, names)
}

fn mapping_methods(annotation: &crate::spring::source_scanner::SourceAnnotation) -> Vec<String> {
    let catalog = http_mapping_methods();
    if let Some(value) = catalog.get(annotation.short_name().as_str()) {
        return vec![value.to_string()];
    }
    let resolver = ValueResolver::new();
    let values = resolver.list_arg(&annotation.args, &["method"]);
    let mut out: Vec<String> = Vec::new();
    for value in values {
        let token = value
            .rsplit('.')
            .next()
            .unwrap_or(&value)
            .trim_matches(|c| c == '{' || c == '}' || c == ' ')
            .to_uppercase();
        if !token.is_empty() {
            out.push(token);
        }
    }
    if out.is_empty() {
        vec!["ANY".to_string()]
    } else {
        out
    }
}

pub fn annotation_values(anns: &super::common::BTreeMapLike, name: &str) -> Vec<String> {
    let Some(ann) = anns.get(name) else {
        return Vec::new();
    };
    // `ann.args.get("value") or ann.args.get("name") or ann.args.get("prefix") or ""`
    let mut chosen: Value = Value::Null;
    for key in ["value", "name", "prefix"] {
        if let Some(value) = ann.args.get(key).filter(|value| crate::pyjson::py_truthy(value)) {
            chosen = value.clone();
            break;
        }
    }
    if !crate::pyjson::py_truthy(&chosen) {
        return Vec::new();
    }
    match &chosen {
        Value::Array(items) => items.iter().map(crate::pyjson::py_str).collect(),
        other => vec![crate::pyjson::py_str(other)],
    }
}

pub fn first_annotation_value(anns: &super::common::BTreeMapLike, name: &str) -> String {
    let values = annotation_values(anns, name);
    values.first().cloned().unwrap_or_default()
}

fn condition_annotations(annotations: &[crate::spring::source_scanner::SourceAnnotation]) -> Vec<String> {
    annotations
        .iter()
        .filter(|ann| ann.short_name().starts_with("Conditional"))
        .map(|ann| ann.raw.clone())
        .collect()
}

//! Port `tools/spring/extractors/crosscutting.py`.

use regex::Regex;
use serde_json::{json, Value};

use super::common::{class_owner_id, fact, first_annotation, rel, stable_hash, FactProps};
use crate::spring::models::SpringFact;
use crate::spring::models::SpringRelationship;
use crate::spring::source_scanner::SourceUnit;
use crate::spring::annotation_catalog::{
    aop_class_annotations, aop_method_annotations, cache_annotations, short_annotation_name, validation_annotations,
};
use crate::spring::value_resolver::{parse_annotation_args, ValueResolver};

pub fn extract_crosscutting_facts(
    units: &[SourceUnit],
    project_id: &str,
    project_name: &str,
) -> (Vec<SpringFact>, Vec<SpringRelationship>) {
    let resolver = ValueResolver::new();
    let mut facts: Vec<SpringFact> = Vec::new();
    let mut relationships: Vec<SpringRelationship> = Vec::new();
    for unit in units {
        for cls in &unit.classes {
            let aspect_ann = first_annotation(&cls.annotations, &aop_class_annotations());
            let mut aspect_id = String::new();
            if aspect_ann.is_some() {
                aspect_id = format!("spring_aspect::{project_id}::{}", stable_hash(&class_owner_id(cls)));
                facts.push(fact(
                    "Aspect",
                    &aspect_id,
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
                    FactProps(vec![]),
                ));
                relationships.push(rel(
                    "SEMANTIC_OF",
                    "Aspect",
                    &aspect_id,
                    "Class",
                    &class_owner_id(cls),
                    project_id,
                    &cls.source,
                    "Aspect class anchor",
                    1.0,
                    "resolved",
                    FactProps(vec![]),
                ));
            }

            for method in &cls.methods {
                if let Some(advice_ann) = first_annotation(&method.annotations, &aop_method_annotations()) {
                    if advice_ann.short_name() == "Pointcut" {
                        let pointcut_id = format!(
                            "spring_pointcut::{project_id}::{}",
                            stable_hash(&format!("{}{}", method.symbol_id(), advice_ann.raw))
                        );
                        facts.push(fact(
                            "Pointcut",
                            &pointcut_id,
                            &format!("{}.{}", cls.name, method.name),
                            &method.source,
                            project_id,
                            project_name,
                            &method.language,
                            1.0,
                            "resolved",
                            "",
                            "",
                            &method.symbol_id(),
                            FactProps(vec![
                                (
                                    "expression".into(),
                                    match advice_ann.args.get("value") {
                                        Some(value) if crate::pyjson::py_truthy(value) => value.clone(),
                                        _ => json!(""),
                                    },
                                ),
                                (
                                    "parsed_clauses".into(),
                                    Value::Array(
                                        pointcut_clauses(&str_arg(&advice_ann.args, "value"))
                                            .into_iter()
                                            .map(|token| json!(token))
                                            .collect(),
                                    ),
                                ),
                            ]),
                        ));
                        if !aspect_id.is_empty() {
                            relationships.push(rel(
                                "DECLARES_POINTCUT",
                                "Aspect",
                                &aspect_id,
                                "Pointcut",
                                &pointcut_id,
                                project_id,
                                &method.source,
                                "@Pointcut declaration",
                                1.0,
                                "resolved",
                                FactProps(vec![]),
                            ));
                        }
                    } else {
                        let advice_id = format!(
                            "spring_advice::{project_id}::{}",
                            stable_hash(&format!("{}{}", method.symbol_id(), advice_ann.raw))
                        );
                        facts.push(fact(
                            "Advice",
                            &advice_id,
                            &format!("{}.{}", cls.name, method.name),
                            &method.source,
                            project_id,
                            project_name,
                            &method.language,
                            1.0,
                            "resolved",
                            "",
                            "",
                            &method.symbol_id(),
                            FactProps(vec![
                                ("advice_kind".into(), Value::String(advice_ann.short_name())),
                                (
                                    "expression".into(),
                                    match advice_ann.args.get("value") {
                                        Some(value) if crate::pyjson::py_truthy(value) => value.clone(),
                                        _ => json!(""),
                                    },
                                ),
                            ]),
                        ));
                        if !aspect_id.is_empty() {
                            relationships.push(rel(
                                "APPLIES_ADVICE",
                                "Aspect",
                                &aspect_id,
                                "Advice",
                                &advice_id,
                                project_id,
                                &method.source,
                                "AOP advice declaration",
                                1.0,
                                "resolved",
                                FactProps(vec![]),
                            ));
                        }
                    }
                }

                for ann in &method.annotations {
                    if !validation_annotations().contains(ann.short_name().as_str()) {
                        continue;
                    }
                    let constraint_id = format!(
                        "spring_validation::{project_id}::{}",
                        stable_hash(&format!("{}{}", method.symbol_id(), ann.raw))
                    );
                    facts.push(fact(
                        "ValidationConstraint",
                        &constraint_id,
                        &ann.short_name(),
                        &method.source,
                        project_id,
                        project_name,
                        &method.language,
                        1.0,
                        "resolved",
                        "",
                        "",
                        &method.symbol_id(),
                        FactProps(vec![
                            ("annotation".into(), Value::String(ann.short_name())),
                            (
                                "message".into(),
                                match ann.args.get("message") {
                                    Some(value) if crate::pyjson::py_truthy(value) => value.clone(),
                                    _ => json!(""),
                                },
                            ),
                            ("groups".into(), json!(resolver.list_arg(&ann.args, &["groups"]))),
                            ("target_position".into(), json!("method")),
                        ]),
                    ));
                    relationships.push(rel(
                        "CONSTRAINED_BY",
                        "Function",
                        &method.symbol_id(),
                        "ValidationConstraint",
                        &constraint_id,
                        project_id,
                        &method.source,
                        "Bean Validation annotation",
                        1.0,
                        "resolved",
                        FactProps(vec![]),
                    ));
                    if ann.short_name() == "Valid" || ann.short_name() == "Validated" {
                        relationships.push(rel(
                            "VALIDATES_CASCADE",
                            "Function",
                            &method.symbol_id(),
                            "ValidationConstraint",
                            &constraint_id,
                            project_id,
                            &method.source,
                            "Validation cascade",
                            1.0,
                            "resolved",
                            FactProps(vec![]),
                        ));
                    }
                }

                for (raw, short_name, args) in parameter_validation_annotations(&method.params) {
                    let constraint_id = format!(
                        "spring_validation::{project_id}::{}",
                        stable_hash(&format!("{}{}:param:{}", method.symbol_id(), "", raw))
                    );
                    facts.push(fact(
                        "ValidationConstraint",
                        &constraint_id,
                        &short_name,
                        &method.source,
                        project_id,
                        project_name,
                        &method.language,
                        1.0,
                        "resolved",
                        "",
                        "",
                        &method.symbol_id(),
                        FactProps(vec![
                            ("annotation".into(), json!(short_name)),
                            (
                                "message".into(),
                                match args.get("message") {
                                    Some(value) if crate::pyjson::py_truthy(value) => value.clone(),
                                    _ => json!(""),
                                },
                            ),
                            ("groups".into(), json!(resolver.list_arg(&args, &["groups"]))),
                            ("target_position".into(), json!("parameter")),
                        ]),
                    ));
                    relationships.push(rel(
                        "CONSTRAINED_BY",
                        "Function",
                        &method.symbol_id(),
                        "ValidationConstraint",
                        &constraint_id,
                        project_id,
                        &method.source,
                        "Bean Validation parameter annotation",
                        1.0,
                        "resolved",
                        FactProps(vec![]),
                    ));
                    if short_name == "Valid" || short_name == "Validated" {
                        relationships.push(rel(
                            "VALIDATES_CASCADE",
                            "Function",
                            &method.symbol_id(),
                            "ValidationConstraint",
                            &constraint_id,
                            project_id,
                            &method.source,
                            "Validation cascade",
                            1.0,
                            "resolved",
                            FactProps(vec![]),
                        ));
                    }
                }

                let cache_catalog = cache_annotations();
                for cache_ann in method
                    .annotations
                    .iter()
                    .filter(|ann| cache_catalog.contains(ann.short_name().as_str()))
                {
                    let mut cache_names = resolver.list_arg(&cache_ann.args, &["cacheNames", "value"]);
                    if cache_names.is_empty() {
                        cache_names = vec!["<unresolved>".to_string()];
                    }
                    let resolved = cache_names != vec!["<unresolved>".to_string()];
                    let operation_id = format!(
                        "spring_cache_op::{project_id}::{}",
                        stable_hash(&format!("{}{}", method.symbol_id(), cache_ann.raw))
                    );
                    facts.push(fact(
                        "CacheOperation",
                        &operation_id,
                        &format!("{}.{}", cls.name, method.name),
                        &method.source,
                        project_id,
                        project_name,
                        &method.language,
                        if resolved { 1.0 } else { 0.6 },
                        if resolved { "resolved" } else { "unresolved" },
                        "",
                        "",
                        &method.symbol_id(),
                        FactProps(vec![
                            ("operation_kind".into(), Value::String(cache_ann.short_name())),
                            ("cache_names".into(), json!(cache_names)),
                            (
                                "key".into(),
                                match cache_ann.args.get("key") {
                                    Some(value) if crate::pyjson::py_truthy(value) => value.clone(),
                                    _ => json!(""),
                                },
                            ),
                            (
                                "condition".into(),
                                match cache_ann.args.get("condition") {
                                    Some(value) if crate::pyjson::py_truthy(value) => value.clone(),
                                    _ => json!(""),
                                },
                            ),
                            (
                                "unless".into(),
                                match cache_ann.args.get("unless") {
                                    Some(value) if crate::pyjson::py_truthy(value) => value.clone(),
                                    _ => json!(""),
                                },
                            ),
                            (
                                "all_entries".into(),
                                json!(crate::pyjson::py_truthy(&cache_ann.args.get("allEntries").cloned().unwrap_or(Value::Bool(false)))),
                            ),
                            (
                                "before_invocation".into(),
                                json!(crate::pyjson::py_truthy(
                                    &cache_ann.args.get("beforeInvocation").cloned().unwrap_or(Value::Bool(false))
                                )),
                            ),
                        ]),
                    ));
                    relationships.push(rel(
                        "APPLIES_TO",
                        "CacheOperation",
                        &operation_id,
                        "Function",
                        &method.symbol_id(),
                        project_id,
                        &method.source,
                        "Cache operation applies to method",
                        1.0,
                        "resolved",
                        FactProps(vec![]),
                    ));
                    let edge_type = match cache_ann.short_name().as_str() {
                        "Cacheable" => "READS_CACHE",
                        "CachePut" => "WRITES_CACHE",
                        _ => "EVICTS_CACHE",
                    };
                    for cache_name in &cache_names {
                        let region_id = format!(
                            "spring_cache_region::{project_id}::default::{}",
                            stable_hash(cache_name)
                        );
                        facts.push(fact(
                            "CacheRegion",
                            &region_id,
                            cache_name,
                            &method.source,
                            project_id,
                            project_name,
                            &method.language,
                            if cache_name != "<unresolved>" { 1.0 } else { 0.6 },
                            if cache_name != "<unresolved>" { "resolved" } else { "unresolved" },
                            "",
                            "",
                            "",
                            FactProps(vec![
                                ("cache_name".into(), json!(cache_name)),
                                ("manager".into(), json!("default")),
                            ]),
                        ));
                        relationships.push(rel(
                            edge_type,
                            "CacheOperation",
                            &operation_id,
                            "CacheRegion",
                            &region_id,
                            project_id,
                            &method.source,
                            &format!("{} cache effect", cache_ann.short_name()),
                            1.0,
                            "resolved",
                            FactProps(vec![]),
                        ));
                    }
                }
            }
        }
    }
    (facts, relationships)
}

fn str_arg(args: &std::collections::BTreeMap<String, Value>, key: &str) -> String {
    match args.get(key) {
        Some(value) if crate::pyjson::py_truthy(value) => crate::pyjson::py_str(value),
        _ => String::new(),
    }
}

fn pointcut_clauses(expression: &str) -> Vec<&'static str> {
    ["execution", "within", "@annotation", "@within", "bean"]
        .into_iter()
        .filter(|token| expression.contains(token))
        .collect()
}

fn parameter_validation_annotations(params: &str) -> Vec<(String, String, std::collections::BTreeMap<String, Value>)> {
    let mut annotations: Vec<(String, String, std::collections::BTreeMap<String, Value>)> = Vec::new();
    let re = Regex::new(r"@([A-Za-z_][\w.]*)(\([^)]*\))?").unwrap();
    for captures in re.captures_iter(params) {
        let name = captures.get(1).map(|m| m.as_str()).unwrap_or("");
        let short = short_annotation_name(name);
        if !validation_annotations().contains(short.as_str()) {
            continue;
        }
        let raw_args = captures.get(2).map(|m| m.as_str()).unwrap_or("");
        let matched = captures.get(0).map(|m| m.as_str()).unwrap_or("");
        annotations.push((matched.to_string(), short, parse_annotation_args(raw_args)));
    }
    annotations
}

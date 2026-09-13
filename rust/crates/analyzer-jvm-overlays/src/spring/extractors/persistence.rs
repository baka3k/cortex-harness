//! Port `tools/spring/extractors/persistence.py`.

use regex::Regex;
use serde_json::{json, Value};

use super::common::{
    class_owner_id, fact, first_annotation, generic_args, parse_extends_types, rel, stable_hash, FactProps,
};
use crate::spring::models::SpringFact;
use crate::spring::models::SpringRelationship;
use crate::spring::source_scanner::{SourceAnnotation, SourceClass, SourceUnit};
use crate::spring::annotation_catalog::{entity_annotations, repository_supertypes, transaction_annotations};

pub fn extract_persistence_facts(
    units: &[SourceUnit],
    project_id: &str,
    project_name: &str,
) -> (Vec<SpringFact>, Vec<SpringRelationship>) {
    let mut facts: Vec<SpringFact> = Vec::new();
    let mut relationships: Vec<SpringRelationship> = Vec::new();
    let mut entity_by_name: std::collections::HashMap<String, String> = std::collections::HashMap::new();

    for unit in units {
        for cls in &unit.classes {
            let Some(entity_ann) = first_annotation(&cls.annotations, &entity_annotations()) else {
                continue;
            };
            let entity_id = format!("spring_entity::{project_id}::{}", stable_hash(&class_owner_id(cls)));
            let entity_name = match entity_ann.args.get("name") {
                Some(value) if crate::pyjson::py_truthy(value) => crate::pyjson::py_str(value),
                _ => cls.name.clone(),
            };
            facts.push(fact(
                "JpaEntity",
                &entity_id,
                &entity_name,
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
                    ("entity_class".into(), json!(cls.qualified_name())),
                    ("entity_kind".into(), Value::String(entity_ann.short_name())),
                    ("table_name".into(), json!(annotation_value(&cls.annotations, "Table", "name"))),
                ]),
            ));
            relationships.push(rel(
                "SEMANTIC_OF",
                "JpaEntity",
                &entity_id,
                "Class",
                &class_owner_id(cls),
                project_id,
                &cls.source,
                "JPA entity anchor",
                1.0,
                "resolved",
                FactProps(vec![]),
            ));
            entity_by_name.insert(cls.name.clone(), entity_id);
        }
    }

    for unit in units {
        for cls in &unit.classes {
            let supers = parse_extends_types(&cls.header);
            let Some(repo_super) = repository_supertype(&supers) else {
                continue;
            };
            let args = generic_args(&repo_super);
            let entity_type = args.first().cloned().unwrap_or_default();
            let id_type = args.get(1).cloned().unwrap_or_default();
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
                    (
                        "repository_kind".into(),
                        json!(repo_super.split('<').next().unwrap_or(&repo_super)),
                    ),
                    ("entity_type".into(), json!(entity_type)),
                    ("id_type".into(), json!(id_type)),
                ]),
            ));
            relationships.push(rel(
                "SEMANTIC_OF",
                "DataRepository",
                &repo_id,
                "Class",
                &class_owner_id(cls),
                project_id,
                &cls.source,
                "Spring Data repository anchor",
                1.0,
                "resolved",
                FactProps(vec![]),
            ));
            let entity_key = entity_type.rsplit('.').next().unwrap_or(&entity_type).to_string();
            if let Some(entity_id) = entity_by_name.get(&entity_key) {
                relationships.push(rel(
                    "MANAGES_ENTITY",
                    "DataRepository",
                    &repo_id,
                    "JpaEntity",
                    entity_id,
                    project_id,
                    &cls.source,
                    "Repository generic entity type",
                    1.0,
                    "resolved",
                    FactProps(vec![]),
                ));
            }

            for method in &cls.methods {
                if let Some(query_ann) = first_annotation(&method.annotations, &one("Query")) {
                    let query_id = format!(
                        "spring_repo_query::{project_id}::{}",
                        stable_hash(&format!("{}{}", method.symbol_id(), query_ann.raw))
                    );
                    facts.push(fact(
                        "DataRepository",
                        &query_id,
                        &method.name,
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
                            ("repository_kind".into(), json!("query_method")),
                            (
                                "query".into(),
                                truthy_or(query_ann.args.get("value")),
                            ),
                            (
                                "native_query".into(),
                                json!(truthy_or_false(query_ann.args.get("nativeQuery"))),
                            ),
                            (
                                "modifying".into(),
                                json!(first_annotation(&method.annotations, &one("Modifying")).is_some()),
                            ),
                        ]),
                    ));
                    relationships.push(rel(
                        "DECLARES_QUERY",
                        "DataRepository",
                        &repo_id,
                        "DataRepository",
                        &query_id,
                        project_id,
                        &method.source,
                        "@Query repository method",
                        1.0,
                        "resolved",
                        FactProps(vec![]),
                    ));
                }
                let derived = parse_derived_query(&method.name);
                if !derived.is_empty() {
                    let derived_id = format!(
                        "spring_repo_derived::{project_id}::{}",
                        stable_hash(&method.symbol_id())
                    );
                    let mut props: Vec<(String, Value)> = Vec::new();
                    for (key, value) in &derived {
                        props.push((key.clone(), value.clone()));
                    }
                    props.push(("return_type".into(), json!(method.return_type)));
                    facts.push(fact(
                        "DataRepository",
                        &derived_id,
                        &method.name,
                        &method.source,
                        project_id,
                        project_name,
                        &method.language,
                        1.0,
                        "resolved",
                        "",
                        "",
                        &method.symbol_id(),
                        FactProps(props),
                    ));
                    relationships.push(rel(
                        "DERIVES_QUERY",
                        "DataRepository",
                        &repo_id,
                        "DataRepository",
                        &derived_id,
                        project_id,
                        &method.source,
                        "Spring Data derived method name",
                        1.0,
                        "resolved",
                        FactProps(vec![]),
                    ));
                }
            }
        }
    }

    for unit in units {
        for cls in &unit.classes {
            let (tx_facts, tx_rels) = transaction_facts_for_class(cls, project_id, project_name);
            facts.extend(tx_facts);
            relationships.extend(tx_rels);
            let (tx_facts, tx_rels) = transaction_facts_for_methods(cls, project_id, project_name);
            facts.extend(tx_facts);
            relationships.extend(tx_rels);
        }
    }
    (facts, relationships)
}

fn truthy_or(value: Option<&Value>) -> Value {
    match value {
        Some(value) if crate::pyjson::py_truthy(value) => value.clone(),
        _ => json!(""),
    }
}

fn truthy_or_false(value: Option<&Value>) -> bool {
    match value {
        Some(value) => crate::pyjson::py_truthy(value),
        None => false,
    }
}

fn one(name: &'static str) -> std::collections::BTreeSet<&'static str> {
    std::collections::BTreeSet::from([name])
}

fn repository_supertype(supers: &[String]) -> Option<String> {
    for item in supers {
        let short = item.split('<').next().unwrap_or(item).rsplit('.').next().unwrap_or(item).trim();
        if repository_supertypes().contains(short) {
            return Some(item.clone());
        }
    }
    None
}

fn parse_derived_query(name: &str) -> std::collections::BTreeMap<String, Value> {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(r"^(find|read|get|query|search|stream|count|exists|delete|remove)(\w*)By(\w+)").unwrap());
    let Some(captures) = re.captures(name) else {
        return Default::default();
    };
    let action = captures.get(1).map(|m| m.as_str()).unwrap_or("");
    let subject = captures.get(2).map(|m| m.as_str()).unwrap_or("");
    let predicate = captures.get(3).map(|m| m.as_str()).unwrap_or("");
    let split_re = Regex::new(r"(And|Or|Between|LessThan|GreaterThan|Like|In|OrderBy)").unwrap();
    // re.split với capture group giữ separator trong kết quả — mô phỏng tay.
    let mut tokens: Vec<Value> = Vec::new();
    let mut last = 0usize;
    for matched in split_re.find_iter(predicate) {
        tokens.push(json!(&predicate[last..matched.start()]));
        tokens.push(json!(matched.as_str()));
        last = matched.end();
    }
    tokens.push(json!(&predicate[last..]));
    std::collections::BTreeMap::from([
        ("action".to_string(), json!(action)),
        ("subject".to_string(), json!(subject)),
        ("predicate".to_string(), json!(predicate)),
        ("tokens".to_string(), Value::Array(tokens)),
    ])
}

fn transaction_facts_for_class(
    cls: &SourceClass,
    project_id: &str,
    project_name: &str,
) -> (Vec<SpringFact>, Vec<SpringRelationship>) {
    let Some(ann) = first_annotation(&cls.annotations, &transaction_annotations()) else {
        return (Vec::new(), Vec::new());
    };
    let tx_id = format!(
        "spring_tx::{project_id}::{}:{}:0",
        stable_hash(&class_owner_id(cls)),
        ann.line
    );
    (
        vec![fact(
            "TransactionBoundary",
            &tx_id,
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
                ("propagation".into(), truthy_or(ann.args.get("propagation"))),
                ("isolation".into(), truthy_or(ann.args.get("isolation"))),
                ("read_only".into(), json!(truthy_or_false(ann.args.get("readOnly")))),
                ("target_kind".into(), json!("class")),
            ]),
        )],
        vec![rel(
            "APPLIES_TO",
            "TransactionBoundary",
            &tx_id,
            "Class",
            &class_owner_id(cls),
            project_id,
            &cls.source,
            "Class @Transactional",
            1.0,
            "resolved",
            FactProps(vec![]),
        )],
    )
}

fn transaction_facts_for_methods(
    cls: &SourceClass,
    project_id: &str,
    project_name: &str,
) -> (Vec<SpringFact>, Vec<SpringRelationship>) {
    let mut facts: Vec<SpringFact> = Vec::new();
    let mut relationships: Vec<SpringRelationship> = Vec::new();
    for (idx, method) in cls.methods.iter().enumerate() {
        let ann = first_annotation(&method.annotations, &transaction_annotations());
        if ann.is_none() && !method.code.contains("TransactionTemplate") {
            continue;
        }
        let tx_id = format!(
            "spring_tx::{project_id}::{}:{}:{}",
            stable_hash(&method.symbol_id()),
            method.source.start_line,
            idx
        );
        let empty: std::collections::BTreeMap<String, Value> = Default::default();
        let args = ann.map(|a| &a.args).unwrap_or(&empty);
        facts.push(fact(
            "TransactionBoundary",
            &tx_id,
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
                ("propagation".into(), truthy_or(args.get("propagation"))),
                ("isolation".into(), truthy_or(args.get("isolation"))),
                ("read_only".into(), json!(truthy_or_false(args.get("readOnly")))),
                ("target_kind".into(), json!("method")),
                (
                    "extraction_method".into(),
                    json!(if ann.is_some() { "annotation" } else { "transaction_template" }),
                ),
            ]),
        ));
        relationships.push(rel(
            "APPLIES_TO",
            "TransactionBoundary",
            &tx_id,
            "Function",
            &method.symbol_id(),
            project_id,
            &method.source,
            "Transactional boundary applies to method",
            1.0,
            "resolved",
            FactProps(vec![]),
        ));
    }
    (facts, relationships)
}

fn annotation_value(annotations: &[SourceAnnotation], ann_name: &str, arg_name: &str) -> String {
    let mut catalog = std::collections::BTreeSet::new();
    catalog.insert(ann_name);
    let Some(ann) = first_annotation(annotations, &catalog) else {
        return String::new();
    };
    // `ann.args.get(arg_name) or ann.args.get("value") or ""`
    for key in [arg_name, "value"] {
        if let Some(value) = ann.args.get(key).filter(|value| crate::pyjson::py_truthy(value)) {
            return crate::pyjson::py_str(value);
        }
    }
    String::new()
}

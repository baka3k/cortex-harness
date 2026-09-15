//! Rust port của `tools/flutter/normalizer.py` — map parser identities và
//! facts thành canonical graph rows cho `LanguageCodeWriter::write_all`
//! (files/classes/types/functions/fields/relations).

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::models::{AnalysisFacts, NodeRecord, ANALYZER_VERSION, PROTOCOL_VERSION};

pub const CANONICAL_CLASS_KINDS: [&str; 2] = ["class", "mixin"];
pub const CANONICAL_TYPE_KINDS: [&str; 5] = ["enum", "extension", "extension_type", "type_alias", "parameter"];
pub const CANONICAL_FUNCTION_KINDS: [&str; 6] = [
    "function", "method", "constructor", "getter", "setter", "operator",
];

/// `stable_symbol_id` — project-scoped ID độc lập vị trí checkout.
pub fn stable_symbol_id(project_id: &str, identity: &str) -> Result<String, String> {
    let scope = project_id.trim();
    if scope.is_empty() {
        return Err("project_id must not be empty".to_string());
    }
    let mut hasher = Sha256::new();
    hasher.update(format!("dart\0{scope}\0{identity}"));
    let digest = hex_prefix(&hasher.finalize(), 24);
    Ok(format!("dart::{scope}::{digest}"))
}

fn hex_prefix(bytes: &[u8], len: usize) -> String {
    let mut out = String::with_capacity(len);
    for byte in bytes {
        if out.len() >= len {
            break;
        }
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

/// `CanonicalBatch` — rows cho writer + mapping identity → symbol id.
#[derive(Default)]
pub struct CanonicalBatch {
    pub files: Vec<Map<String, Value>>,
    pub classes: Vec<Map<String, Value>>,
    pub types: Vec<Map<String, Value>>,
    pub functions: Vec<Map<String, Value>>,
    pub fields: Vec<Map<String, Value>>,
    pub relations: Vec<Map<String, Value>>,
    pub identity_to_id: BTreeMap<String, String>,
}

fn string_prop(properties: &BTreeMap<String, Value>, key: &str) -> String {
    properties
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn bool_prop(properties: &BTreeMap<String, Value>, key: &str, default: bool) -> bool {
    properties.get(key).and_then(Value::as_bool).unwrap_or(default)
}

fn string_list(properties: &BTreeMap<String, Value>, key: &str) -> Vec<Value> {
    properties
        .get(key)
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
}

/// `_base_row` — 1:1 các key của Python normalizer.
#[allow(clippy::too_many_arguments)]
fn base_row(
    node: &NodeRecord,
    node_id: &str,
    project_id: &str,
    project_name: &str,
    repo: &str,
    build_system: &str,
) -> Map<String, Value> {
    let properties = &node.properties;
    let name = string_prop(properties, "name");
    let name = if name.is_empty() {
        string_prop(properties, "path")
    } else {
        name
    };
    let name = if name.is_empty() {
        node.identity.clone()
    } else {
        name
    };
    let qualified_name = {
        let value = string_prop(properties, "qualified_name");
        if value.is_empty() {
            name.clone()
        } else {
            value
        }
    };
    let code = string_prop(properties, "code");
    let comment = string_prop(properties, "comment");
    let note = {
        let value = string_prop(properties, "note");
        if !value.is_empty() {
            value
        } else if !comment.is_empty() {
            comment.clone()
        } else {
            code.chars().take(4000).collect()
        }
    };
    let package_name = {
        let value = string_prop(properties, "package_uri");
        if value.is_empty() {
            string_prop(properties, "package_name")
        } else {
            value
        }
    };
    let class_name = string_prop(properties, "class_name");
    let scope_name = {
        let value = string_prop(properties, "scope_name");
        if value.is_empty() {
            class_name.clone()
        } else {
            value
        }
    };
    let exported_default = !name.starts_with('_');
    let arity = properties.get("arity").and_then(Value::as_i64).unwrap_or(0);
    Map::from_iter([
        ("id".to_string(), json!(node_id)),
        ("name".to_string(), json!(name)),
        ("qualified_name".to_string(), json!(qualified_name)),
        ("kind".to_string(), json!(node.kind)),
        ("package_name".to_string(), json!(package_name)),
        ("class_name".to_string(), json!(class_name)),
        ("scope_name".to_string(), json!(scope_name)),
        ("file_path".to_string(), json!(node.evidence.file)),
        ("path".to_string(), json!(node.evidence.file)),
        ("start_byte".to_string(), json!(node.evidence.offset)),
        (
            "end_byte".to_string(),
            json!(node.evidence.offset + node.evidence.length),
        ),
        ("start_line".to_string(), json!(node.evidence.start_line)),
        ("end_line".to_string(), json!(node.evidence.end_line)),
        ("arity".to_string(), json!(arity)),
        ("code".to_string(), json!(code)),
        ("comment".to_string(), json!(comment)),
        ("summary".to_string(), json!("")),
        ("note".to_string(), json!(note)),
        ("imports".to_string(), Value::Array(string_list(properties, "imports"))),
        ("exports".to_string(), Value::Array(string_list(properties, "exports"))),
        (
            "exported".to_string(),
            json!(bool_prop(properties, "exported", exported_default)),
        ),
        ("external".to_string(), json!(bool_prop(properties, "external", false))),
        ("builtin".to_string(), json!(bool_prop(properties, "builtin", false))),
        ("react_role".to_string(), json!("")),
        ("middleware_kind".to_string(), json!("")),
        (
            "type_signature".to_string(),
            json!(string_prop(properties, "type_signature")),
        ),
        ("project_id".to_string(), json!(project_id)),
        ("project_name".to_string(), json!(project_name)),
        ("language".to_string(), json!("dart")),
        ("repo".to_string(), json!(repo)),
        ("build_system".to_string(), json!(build_system)),
        ("source_identity".to_string(), json!(node.identity)),
        ("generated".to_string(), json!(bool_prop(properties, "generated", false))),
    ])
}

/// `normalize_facts` — skip generated (trừ khi include_generated), giữ id
/// mapping cho endpoint `_reference_only`, bucket rows theo kind, sort id.
pub fn normalize_facts(
    facts: &AnalysisFacts,
    project_name: Option<&str>,
    repo: &str,
    build_system: &str,
) -> Result<CanonicalBatch, String> {
    let project_id = &facts.header.project_id;
    let mut batch = CanonicalBatch::default();
    let mut included: Vec<&NodeRecord> = Vec::new();
    for node in &facts.nodes {
        if bool_prop(&node.properties, "generated", false) {
            continue;
        }
        batch
            .identity_to_id
            .insert(node.identity.clone(), stable_symbol_id(project_id, &node.identity)?);
        if bool_prop(&node.properties, "_reference_only", false) {
            continue;
        }
        included.push(node);
    }
    let project_name = project_name.unwrap_or(project_id);
    for node in included {
        let row = base_row(
            node,
            &batch.identity_to_id[&node.identity],
            project_id,
            project_name,
            repo,
            build_system,
        );
        if node.kind == "file" {
            batch.files.push(row);
        } else if CANONICAL_CLASS_KINDS.contains(&node.kind.as_str()) {
            batch.classes.push(row);
        } else if CANONICAL_TYPE_KINDS.contains(&node.kind.as_str()) {
            batch.types.push(row);
        } else if CANONICAL_FUNCTION_KINDS.contains(&node.kind.as_str()) {
            batch.functions.push(row);
        } else if node.kind == "field" {
            batch.fields.push(row);
        }
    }
    for edge in &facts.edges {
        let Some(source_id) = batch.identity_to_id.get(&edge.source) else {
            continue;
        };
        let Some(target_id) = batch.identity_to_id.get(&edge.target) else {
            continue;
        };
        let mut properties = edge.properties.clone();
        properties.insert("confidence".to_string(), json!(edge.confidence));
        properties.insert("source_file".to_string(), json!(edge.evidence.file));
        properties.insert("source_line".to_string(), json!(edge.evidence.start_line));
        properties.insert("project_id".to_string(), json!(project_id));
        properties.insert("analyzer_version".to_string(), json!(ANALYZER_VERSION));
        properties.insert("protocol_version".to_string(), json!(PROTOCOL_VERSION));
        let properties_map: Map<String, Value> = properties.into_iter().collect();
        batch.relations.push(Map::from_iter([
            ("source_id".to_string(), json!(source_id)),
            ("target_id".to_string(), json!(target_id)),
            ("rel_type".to_string(), json!(edge.relationship)),
            ("properties".to_string(), Value::Object(properties_map)),
        ]));
    }
    for values in [
        &mut batch.files,
        &mut batch.classes,
        &mut batch.types,
        &mut batch.functions,
        &mut batch.fields,
    ] {
        values.sort_by(|left, right| {
            left.get("id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .cmp(right.get("id").and_then(Value::as_str).unwrap_or_default())
        });
    }
    batch.relations.sort_by(|left, right| {
        let key = |row: &Map<String, Value>| {
            (
                row.get("rel_type").and_then(Value::as_str).unwrap_or_default().to_string(),
                row.get("source_id").and_then(Value::as_str).unwrap_or_default().to_string(),
                row.get("target_id").and_then(Value::as_str).unwrap_or_default().to_string(),
            )
        };
        key(left).cmp(&key(right))
    });
    Ok(batch)
}

/// Gom id của mọi symbol row (keep_ids cho incremental cleanup flutter).
pub fn symbol_keep_ids(batch: &CanonicalBatch) -> BTreeSet<String> {
    [&batch.files, &batch.classes, &batch.types, &batch.functions, &batch.fields]
        .into_iter()
        .flatten()
        .filter_map(|row| row.get("id").and_then(Value::as_str).map(str::to_string))
        .collect()
}

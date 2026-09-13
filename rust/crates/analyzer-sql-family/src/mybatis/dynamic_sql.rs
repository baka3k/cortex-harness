//! Port `tools/mybatis/dynamic_sql.py`.

use regex::Regex;

use crate::mybatis::models::{DynamicNodeFact, SourceSpan};

pub const DYNAMIC_TAGS: [&str; 10] = [
    "if", "choose", "when", "otherwise", "foreach", "trim", "where", "set", "bind", "script",
];

/// `ognl_identifiers`.
pub fn ognl_identifiers(text: &str) -> Vec<String> {
    const IGNORED: [&str; 12] = [
        "and", "or", "not", "null", "true", "false", "eq", "ne", "lt", "le", "gt", "ge",
    ];
    let re = Regex::new(r"[A-Za-z_][A-Za-z0-9_.$]*").unwrap();
    let mut rows: Vec<String> = Vec::new();
    for token in re.find_iter(text) {
        let head = token.as_str().split('.').next().unwrap_or(token.as_str());
        if !IGNORED.contains(&head) && !rows.iter().any(|item| item == head) {
            rows.push(head.to_string());
        }
    }
    rows
}

/// `dynamic_node` constructor.
#[allow(clippy::too_many_arguments)]
pub fn dynamic_node(
    owner_id: &str,
    tag: &str,
    node_kind: &str,
    source: SourceSpan,
    order: i64,
    text: String,
    attributes: std::collections::BTreeMap<String, String>,
    branch_role: &str,
) -> DynamicNodeFact {
    let attrs = attributes;
    let test = attrs.get("test").cloned().unwrap_or_default();
    let stable_id = format!(
        "mybatis_dynamic::{owner_id}::{}:{}:{order}",
        source.start_line, source.start_column
    );
    let mut refs = ognl_identifiers(&test);
    if tag == "foreach" {
        let collection = attrs.get("collection").cloned().unwrap_or_default();
        if !collection.is_empty() && !refs.contains(&collection) {
            refs.push(collection);
        }
    }
    if tag == "bind" {
        for value in ognl_identifiers(&attrs.get("value").cloned().unwrap_or_default()) {
            if !refs.contains(&value) {
                refs.push(value);
            }
        }
    }
    DynamicNodeFact {
        stable_id,
        owner_id: owner_id.to_string(),
        tag: tag.to_string(),
        node_kind: node_kind.to_string(),
        source,
        order,
        text,
        attributes: attrs,
        test,
        branch_role: branch_role.to_string(),
        referenced_variables: refs,
    }
}

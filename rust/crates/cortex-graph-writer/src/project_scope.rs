//! Port của `tools/common/project_scope.py` — phần writer cần
//! (`normalize_project_id`, `project_id_lookup_key`, `enrich_project_scope`,
//! `prepare_project_scope_parameters`). Docstring gốc:
//!
//! * omitted/blank `project_id` → unscoped query across every project;
//! * a given `project_id` is matched case-insensitively (`casefold()`);
//! * write/sync paths keep exact equality so one project can never mutate
//!   another.

use std::collections::BTreeMap;

use serde_json::{Map, Value};

pub const PROJECT_ID_NORMALIZED_FIELD: &str = "project_id_normalized";

const PROJECT_SCOPE_PARAMETER_KEYS: [&str; 6] = [
    "project_id",
    "be_project_id",
    "fe_project_id",
    "be_project",
    "fe_project",
    "pid",
];

/// `normalize_project_id`: non-empty str(value).strip() hoặc None.
pub fn normalize_project_id(value: Option<&str>) -> Option<String> {
    let raw = value?;
    let normalized = raw.trim();
    if normalized.is_empty() {
        None
    } else {
        Some(normalized.to_string())
    }
}

/// `project_id_lookup_key`: casefold của normalize.
pub fn project_id_lookup_key(value: Option<&str>) -> Option<String> {
    normalize_project_id(value).map(|normalized| normalized.to_lowercase())
}

/// `enrich_project_scope`: copy map, thêm `project_id_normalized` khi có
/// `project_id` (đệ quy vào list/map). Rows của writer luôn đi qua hàm này ở
/// store layer (qua `prepare_project_scope_parameters`).
pub fn enrich_project_scope_map(value: &Map<String, Value>) -> Map<String, Value> {
    let mut enriched = value.clone();
    if let Some(project_id) = enriched.get("project_id") {
        let lookup = project_id_lookup_key(project_id.as_str());
        match lookup {
            Some(key) => {
                enriched.insert(
                    PROJECT_ID_NORMALIZED_FIELD.to_string(),
                    Value::String(key),
                );
            }
            None => {
                enriched.remove(PROJECT_ID_NORMALIZED_FIELD);
            }
        }
    }
    enriched
}

/// `prepare_project_scope_parameters`: mỗi recognized scope param nhận sibling
/// `*_normalized`. Trả params mới (không đổi input).
pub fn prepare_project_scope_parameters(
    parameters: &BTreeMap<String, Value>,
) -> BTreeMap<String, Value> {
    let mut prepared: BTreeMap<String, Value> = BTreeMap::new();
    for (key, value) in parameters {
        // enrich từng param: param-map có project_id → thêm normalized field.
        match value {
            Value::Object(map) => {
                prepared.insert(key.clone(), Value::Object(enrich_project_scope_map(map)));
            }
            other => {
                prepared.insert(key.clone(), other.clone());
            }
        }
    }
    for key in PROJECT_SCOPE_PARAMETER_KEYS {
        if let Some(value) = prepared.get(key) {
            let lookup = project_id_lookup_key(value.as_str());
            if let Some(normalized) = lookup {
                prepared.insert(
                    format!("{key}_normalized"),
                    Value::String(normalized),
                );
            }
        }
    }
    prepared
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn lookup_key_casefolds_and_trims() {
        assert_eq!(project_id_lookup_key(Some("  Bank ")), Some("bank".to_string()));
        assert_eq!(project_id_lookup_key(Some("")), None);
        assert_eq!(project_id_lookup_key(None), None);
    }

    #[test]
    fn prepare_adds_normalized_sibling() {
        let mut params = BTreeMap::new();
        params.insert("project_id".to_string(), json!("Bank"));
        params.insert("limit".to_string(), json!(5));
        let prepared = prepare_project_scope_parameters(&params);
        assert_eq!(prepared.get("project_id_normalized").unwrap(), "bank");
        assert!(!prepared.contains_key("limit_normalized"));
    }

    #[test]
    fn enrich_rows_add_normalized_field() {
        let mut row = Map::new();
        row.insert("project_id".to_string(), json!("Stock"));
        let enriched = enrich_project_scope_map(&row);
        assert_eq!(
            enriched.get(PROJECT_ID_NORMALIZED_FIELD).unwrap(),
            &json!("stock")
        );
    }
}

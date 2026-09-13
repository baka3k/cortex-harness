//! Canonical JSON emission — deterministic regardless of the
//! `serde_json/preserve_order` feature (Cargo feature unification can flip
//! `serde_json::Map` from `BTreeMap` to `IndexMap` for whole-workspace
//! builds). Mirrors `cortex-graph-core::identity::canonical_json`:
//! recursively sort object keys before serializing.

use serde_json::Value;

pub fn canonical_json(value: &Value) -> String {
    serde_json::to_string(&canonicalize(value)).expect("canonical json serialization")
}

fn canonicalize(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let sorted: BTreeMap<String, Value> = map
                .iter()
                .map(|(k, v)| (k.clone(), canonicalize(v)))
                .collect();
            Value::Object(sorted.into_iter().collect())
        }
        Value::Array(items) => Value::Array(items.iter().map(canonicalize).collect()),
        other => other.clone(),
    }
}

use std::collections::BTreeMap;

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn object_keys_sorted_regardless_of_map_flavor() {
        let value = json!({
            "zeta": 1,
            "alpha": {"y": 2, "b": 3},
            "mid": [{"d": 4, "a": 5}],
        });
        assert_eq!(
            canonical_json(&value),
            r#"{"alpha":{"b":3,"y":2},"mid":[{"a":5,"d":4}],"zeta":1}"#
        );
    }

    #[test]
    fn arrays_keep_order() {
        let value = json!(["b", "a"]);
        assert_eq!(canonical_json(&value), r#"["b","a"]"#);
    }
}

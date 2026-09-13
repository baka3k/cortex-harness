//! Port của `code-tiny/tools/graph/journal/identity.py` — canonical identities.

use serde::Serialize;
use sha2::{Digest, Sha256};

/// Python: `json.dumps(value, ensure_ascii=False, allow_nan=False,
/// separators=(",", ":"), sort_keys=True).encode("utf-8")`.
///
/// serde_json `Map` là BTreeMap nên `to_string` cho keys sorted + compact —
/// khớp byte-for-byte với Python cho các giá trị JSON tương ứng
/// (string/int/bool/null/list/map; non-ASCII giữ nguyên như `ensure_ascii=False`).
pub fn canonical_json(value: &serde_json::Value) -> Vec<u8> {
    // Feature unification: crate khác bật serde_json/preserve_order khiến Map
    // thành insertion-ordered — canonical hoá PHẢI sort tường minh để stable-id
    // deterministic bất kể feature của workspace.
    let canonical_value = canonicalize(value);
    serde_json::to_string(&canonical_value)
        .expect("canonical json serialize")
        .into_bytes()
}

fn canonicalize(value: &serde_json::Value) -> serde_json::Value {
    use serde_json::Value;
    match value {
        Value::Object(map) => {
            let sorted: std::collections::BTreeMap<&String, &serde_json::Value> =
                map.iter().collect();
            Value::Object(
                sorted
                    .into_iter()
                    .map(|(key, item)| (key.clone(), canonicalize(item)))
                    .collect(),
            )
        }
        Value::Array(items) => Value::Array(items.iter().map(canonicalize).collect()),
        other => other.clone(),
    }
}

pub fn sha256_hex(value: &[u8]) -> String {
    let digest = Sha256::digest(value);
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

pub fn run_fingerprint(metadata: &serde_json::Value) -> String {
    sha256_hex(&canonical_json(metadata))
}

pub fn run_id(metadata: &serde_json::Value) -> String {
    format!("run_{}", run_fingerprint(metadata))
}

/// Đối số của `deterministic_job_id` — replicate identity dict của Python.
#[derive(Serialize)]
pub struct JobIdentity<'a> {
    pub run_fingerprint: &'a str,
    pub phase: &'a str,
    pub operation_key: &'a str,
    pub sequence: i64,
    pub payload_sha256: &'a str,
}

pub fn deterministic_job_id(identity: &JobIdentity<'_>) -> String {
    let value = serde_json::json!({
        "run_fingerprint": identity.run_fingerprint,
        "phase": identity.phase,
        "operation_key": identity.operation_key,
        "sequence": identity.sequence,
        "payload_sha256": identity.payload_sha256,
    });
    format!("job_{}", sha256_hex(&canonical_json(&value)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn canonical_json_sorts_keys_and_compacts() {
        let value = json!({"b": 1, "a": {"d": [1, 2], "c": "xé"}});
        let bytes = canonical_json(&value);
        assert_eq!(String::from_utf8(bytes).unwrap(), r#"{"a":{"c":"xé","d":[1,2]},"b":1}"#);
    }

    #[test]
    fn run_id_prefix() {
        let metadata = json!({"parser": "python"});
        assert!(run_id(&metadata).starts_with("run_"));
        assert_eq!(run_id(&metadata).len(), 4 + 64);
    }

    #[test]
    fn job_id_is_deterministic() {
        let identity = JobIdentity {
            run_fingerprint: "fp",
            phase: "nodes",
            operation_key: "node.upsert",
            sequence: 3,
            payload_sha256: "abc",
        };
        assert_eq!(deterministic_job_id(&identity), deterministic_job_id(&identity));
        assert!(deterministic_job_id(&identity).starts_with("job_"));
    }
}

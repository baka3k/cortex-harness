//! Golden parity test cho schema manifest (nguồn: `tools/graph/schema/manifest.py`).

use cortex_graph_core::schema_manifest::code_graph_schema;
use serde::Deserialize;
use std::collections::BTreeMap;

#[derive(Deserialize)]
struct Fixture {
    fingerprint: String,
    name: String,
    version: u32,
    index_count: usize,
    driver_indexes: Vec<serde_json::Value>,
    has_identity: BTreeMap<String, bool>,
}

#[test]
fn golden_parity_against_python() {
    let fixture: Fixture =
        serde_json::from_str(include_str!("fixtures/manifest_golden.json")).expect("fixture hợp lệ");

    let manifest = code_graph_schema();
    assert_eq!(manifest.name, fixture.name);
    assert_eq!(manifest.version, fixture.version);
    assert_eq!(manifest.indexes.len(), fixture.index_count);
    assert_eq!(
        manifest.fingerprint(),
        fixture.fingerprint,
        "fingerprint phải khớp Python (canonical JSON + sha256[:16])"
    );

    let driver_indexes = manifest.driver_indexes();
    let expected = serde_json::to_value(&fixture.driver_indexes).unwrap();
    assert_eq!(
        serde_json::to_value(&driver_indexes).unwrap(),
        expected,
        "driver_indexes phải khớp Python"
    );

    for (key, expected_value) in &fixture.has_identity {
        let (label, property) = key.split_once('/').expect("key dạng Label/property");
        assert_eq!(
            manifest.has_identity_index(label, property),
            *expected_value,
            "has_identity_index({label}, {property})"
        );
    }
}

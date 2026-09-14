//! Golden parity tests — the Rust serving layer is compared against
//! fixtures generated from the *Python* modules by
//! `scripts/rust_mcp/generate_data.py`:
//!
//! * `catalog_served.json`       ← `tool_metadata.build_catalog(_UNIFIED_TOOL_NAMES)`
//! * `capability_catalog.json`   ← `framework_registry.capability_catalog()`
//! * `list_parsers_summary.json` / `list_parsers_full.json` ← the unified
//!   `tool_list_parsers` payload (pure functions of framework_registry)
//! * `parser_aliases.json`       ← `sorted(parser_aliases())`
//! * `framework_relationships.json` ← per-parser `relationships_for()`

use cortex_mcp::catalog;
use cortex_mcp::dispatch;
use cortex_mcp::framework_registry;
use serde_json::Value;

fn fixture(name: &str) -> Value {
    let text = std::fs::read_to_string(format!(
        "{}/tests/fixtures/{name}",
        env!("CARGO_MANIFEST_DIR")
    ))
    .unwrap_or_else(|error| panic!("fixture {name} missing (run scripts/rust_mcp/generate_data.py): {error}"));
    serde_json::from_str(&text).expect("fixture JSON")
}

fn assert_deep_equal(actual: &Value, expected: &Value, path: &str) {
    if actual == expected {
        return;
    }
    match (actual, expected) {
        (Value::Object(actual_map), Value::Object(expected_map)) => {
            for key in expected_map.keys() {
                assert!(
                    actual_map.contains_key(key),
                    "{path}: missing key {key} (actual={actual})"
                );
            }
            for (key, expected_value) in expected_map {
                let actual_value = actual_map
                    .get(key)
                    .unwrap_or_else(|| panic!("{path}: missing key {key}"));
                assert_deep_equal(actual_value, expected_value, &format!("{path}.{key}"));
            }
        }
        (Value::Array(actual_items), Value::Array(expected_items)) => {
            assert_eq!(
                actual_items.len(),
                expected_items.len(),
                "{path}: array length differs"
            );
            for (index, (a, e)) in actual_items.iter().zip(expected_items.iter()).enumerate() {
                assert_deep_equal(a, e, &format!("{path}[{index}]"));
            }
        }
        _ => panic!(
            "{path}: byte mismatch\n  rust   = {actual}\n  python = {expected}"
        ),
    }
}

#[test]
fn list_mcp_functions_matches_python_build_catalog() {
    let expected = fixture("catalog_served.json");
    let payload = catalog::list_mcp_functions_payload();
    assert_eq!(payload["total_count"], expected.as_array().unwrap().len() as u64);
    let functions = payload["functions"].as_array().unwrap();
    assert_deep_equal(&Value::Array(functions.clone()), &expected, "functions");
}

#[test]
fn capability_catalog_matches_python() {
    let expected = fixture("capability_catalog.json");
    let actual = Value::Array(framework_registry::capability_catalog());
    assert_deep_equal(&actual, &expected, "capability_catalog");
}

#[test]
fn list_parsers_summary_matches_python() {
    let expected = fixture("list_parsers_summary.json");
    let actual = dispatch::tool_list_parsers(Some("summary")).unwrap();
    assert_deep_equal(&actual, &expected, "list_parsers[summary]");
}

#[test]
fn list_parsers_full_matches_python() {
    let expected = fixture("list_parsers_full.json");
    let actual = dispatch::tool_list_parsers(Some("full")).unwrap();
    assert_deep_equal(&actual, &expected, "list_parsers[full]");
}

#[test]
fn parser_aliases_match_python() {
    let expected = fixture("parser_aliases.json");
    let actual = framework_registry::parser_aliases(None);
    assert_deep_equal(&Value::Array(actual.iter().map(|alias| Value::String(alias.clone())).collect()), &expected, "parser_aliases");
}

#[test]
fn framework_relationships_match_python() {
    let expected = fixture("framework_relationships.json");
    for (parser, relationships) in expected.as_object().unwrap() {
        let actual = framework_registry::default_relationships(Some(parser), None);
        assert_deep_equal(
            &Value::Array(actual.iter().map(|item| Value::String(item.clone())).collect()),
            relationships,
            &format!("relationships[{parser}]"),
        );
    }
}

#[test]
fn catalog_data_file_is_the_serving_source() {
    // `data/catalog.json` is generated verbatim from `_FULL_CATALOG` (43
    // entries, fan-out parser_type input already injected by the Python
    // module import).
    let full = catalog::full_catalog();
    assert_eq!(full.len(), 43);
    let fanout = full
        .iter()
        .filter(|entry| entry["name"] == "search_functions")
        .find_map(|entry| {
            entry["inputs"].as_array().map(|inputs| {
                inputs
                        .iter()
                        .any(|input| input["name"] == "parser_type")
            })
        })
        .unwrap();
    assert!(fanout, "search_functions must carry the injected parser_type input");
}

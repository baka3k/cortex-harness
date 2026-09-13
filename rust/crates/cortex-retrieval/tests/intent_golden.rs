//! Golden parity test cho `intent` (nguồn: `query_intent_classifier.py`).

use cortex_retrieval::intent::{classify_query, classify_query_explain, get_weight_profile};
use serde::Deserialize;
use std::collections::BTreeMap;

#[derive(Deserialize)]
struct Fixture {
    classify: Vec<IntentCase>,
    explain: Vec<ExplainCase>,
    weights: Vec<WeightsCase>,
}

#[derive(Deserialize)]
struct IntentCase {
    query: String,
    expected: String,
}

#[derive(Deserialize)]
struct ExplainCase {
    query: String,
    expected: ExpectedExplain,
}

#[derive(Deserialize)]
struct ExpectedExplain {
    intent: String,
    #[serde(default)]
    matched: String,
}

#[derive(Deserialize)]
struct WeightsCase {
    intent: String,
    expected: BTreeMap<String, f64>,
}

#[test]
fn golden_parity_against_python() {
    let fixture: Fixture =
        serde_json::from_str(include_str!("fixtures/intent_golden.json")).expect("fixture hợp lệ");

    for case in &fixture.classify {
        assert_eq!(
            classify_query(&case.query),
            case.expected,
            "classify query={:?}",
            case.query
        );
    }

    for case in &fixture.explain {
        let actual = classify_query_explain(&case.query);
        assert_eq!(
            actual.intent, case.expected.intent,
            "explain intent query={:?}",
            case.query
        );
        assert_eq!(
            actual.matched, case.expected.matched,
            "explain matched query={:?}",
            case.query
        );
    }

    for case in &fixture.weights {
        let actual = get_weight_profile(&case.intent);
        let actual_map: BTreeMap<&str, f64> = actual.into_iter().collect();
        assert_eq!(actual_map.len(), case.expected.len(), "weights len");
        for (key, value) in &case.expected {
            assert_eq!(actual_map[key.as_str()], *value, "weights[{key}]");
        }
    }
}

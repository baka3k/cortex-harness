//! Golden parity test cho fusion pipeline (nguồn: `intelligent_retrieval.py`
//! chạy thật với seeds stubbed — xem `scripts/rust_parity/gen_fusion_fixtures.py`).

use cortex_retrieval::bm25::{Bm25Ranker, Document};
use cortex_retrieval::fusion::{
    candidate_from_keyword_node, candidate_from_qdrant_hit, FusionEngine, QdrantHit, SeedInputs,
};
use serde::Deserialize;
use serde_json::Value;
use std::collections::{BTreeMap, HashMap};

#[derive(Deserialize)]
struct Fixture {
    cases: Vec<Case>,
}

#[derive(Deserialize)]
struct Case {
    name: String,
    query: String,
    top_k: usize,
    debug: bool,
    weight_override: Option<BTreeMap<String, f64>>,
    now_epoch: f64,
    bm25_docs: Option<Vec<Bm25Doc>>,
    freshness_map: BTreeMap<String, String>,
    dirty: Vec<String>,
    qdrant_hits: Vec<QdrantHit>,
    keyword_nodes: Vec<BTreeMap<String, Value>>,
    expected: Vec<Value>,
}

#[derive(Deserialize)]
struct Bm25Doc {
    symbol_id: String,
    note: String,
}

const TOLERANCE: f64 = 1e-9;

#[test]
fn golden_parity_against_python() {
    let fixture: Fixture =
        serde_json::from_str(include_str!("fixtures/fusion_golden.json")).expect("fixture hợp lệ");

    for case in &fixture.cases {
        let engine = FusionEngine {
            freshness_map: case.freshness_map.clone(),
            dirty_set: case.dirty.iter().cloned().collect(),
            bm25_ranker: case.bm25_docs.as_ref().map(|docs| {
                let mut ranker = Bm25Ranker::new();
                ranker.build_index(
                    &docs
                        .iter()
                        .map(|d| Document::new(d.symbol_id.clone(), d.note.clone()))
                        .collect::<Vec<_>>(),
                );
                ranker
            }),
            bm25_weight: FusionEngine::bm25_weight_default(),
        };

        let seeds = SeedInputs {
            qdrant: case.qdrant_hits.iter().map(candidate_from_qdrant_hit).collect(),
            keyword: case
                .keyword_nodes
                .iter()
                .map(candidate_from_keyword_node)
                .collect(),
        };
        let overrides: Vec<(&str, f64)> = case
            .weight_override
            .as_ref()
            .map(|m| m.iter().map(|(k, v)| (k.as_str(), *v)).collect())
            .unwrap_or_default();

        let results = engine.search(
            &case.query,
            &seeds,
            case.top_k,
            case.debug,
            &overrides,
            case.now_epoch,
        );

        assert_eq!(
            results.len(),
            case.expected.len(),
            "case `{}`: số kết quả khác Python",
            case.name
        );
        for (actual, expected) in results.iter().zip(&case.expected) {
            let obj = expected.as_object().expect("expected là object");
            assert_eq!(
                &actual.node_id,
                obj["node_id"].as_str().unwrap(),
                "case `{}` node_id",
                case.name
            );
            let score = obj["score"].as_f64().unwrap();
            assert!(
                (actual.score - score).abs() <= TOLERANCE,
                "case `{}` node {} score: rust={} python={score}",
                case.name,
                actual.node_id,
                actual.score
            );
            compare_node(&actual.node, obj.get("node").expect("node"), case);
            compare_explanation(&actual.explanation, obj.get("explanation"), case);
        }
    }
}

/// So node dict: duyệt keys của expected (Python dict) — key thừa của Rust
/// (candidate tối thiểu được Rust serialize đủ field) bỏ qua.
fn compare_node(node: &cortex_retrieval::fusion::Candidate, expected: &Value, case: &Case) {
    let actual = serde_json::to_value(node).expect("serialize candidate");
    let expected_map = expected.as_object().expect("node là object");
    for (key, expected_value) in expected_map {
        let actual_value = actual.get(key).unwrap_or_else(|| {
            panic!("case `{}`: node thiếu key `{key}`", case.name)
        });
        match (actual_value.as_f64(), expected_value.as_f64()) {
            (Some(a), Some(b)) => assert!(
                (a - b).abs() <= TOLERANCE,
                "case `{}` node.{key}: rust={a} python={b}",
                case.name
            ),
            _ => assert_eq!(
                actual_value, expected_value,
                "case `{}` node.{key}",
                case.name
            ),
        }
    }
}

/// So explanation phẳng kiểu Python: `{**raw_signals, "weighted_contributions",
/// "query_intent", "weights_used"}`.
fn compare_explanation(
    explanation: &Option<cortex_retrieval::fusion::Explanation>,
    expected: Option<&Value>,
    case: &Case,
) {
    match (explanation, expected) {
        (None, None) => (),
        (None, Some(_)) => panic!("case `{}`: thiếu explanation", case.name),
        (Some(_), None) => panic!("case `{}`: thừa explanation", case.name),
        (Some(expl), Some(expected)) => {
            let expected_map = expected.as_object().expect("explanation là object");
            for (key, value) in expected_map {
                match key.as_str() {
                    "weighted_contributions" => {
                        let contributions = &expl.weighted_contributions;
                        for (sk, sv) in value.as_object().unwrap() {
                            let a = contributions[sk.as_str()];
                            let b = sv.as_f64().unwrap();
                            assert!(
                                (a - b).abs() <= TOLERANCE,
                                "case `{}` contribution.{sk}: rust={a} python={b}",
                                case.name
                            );
                        }
                    }
                    "query_intent" => assert_eq!(
                        expl.query_intent.expect("intent").to_string(),
                        value.as_str().unwrap(),
                        "case `{}` query_intent",
                        case.name
                    ),
                    "weights_used" => {
                        let weights = expl.weights_used.as_ref().expect("weights_used");
                        for (wk, wv) in value.as_object().unwrap() {
                            let a = weights[wk.as_str()];
                            let b = wv.as_f64().unwrap();
                            assert!(
                                (a - b).abs() <= TOLERANCE,
                                "case `{}` weights_used.{wk}: rust={a} python={b}",
                                case.name
                            );
                        }
                    }
                    raw_signal => {
                        let a = expl.raw_signals[raw_signal];
                        let b = value.as_f64().unwrap();
                        assert!(
                            (a - b).abs() <= TOLERANCE,
                            "case `{}` signal {raw_signal}: rust={a} python={b}",
                            case.name
                        );
                    }
                }
            }
        }
    }
}

// HashMap import giữ cho struct FusionEngine literal compile (field type).
#[allow(dead_code)]
fn _type_check(_: HashMap<String, String>) {}

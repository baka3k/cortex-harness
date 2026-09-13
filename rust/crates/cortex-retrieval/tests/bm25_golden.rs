//! Golden parity test: so `Bm25Ranker` Rust với output của implementation Python
//! (`tools/common/bm25_ranker.py` + `rank_bm25` 0.2.2) trên fixtures sinh sẵn.
//!
//! Regenerate fixtures:
//! `uv run --no-project --with rank_bm25==0.2.2 python scripts/rust_parity/gen_bm25_fixtures.py`
//! (chạy từ repo root). Fixtures được commit — diff của nó = drift detection.

use cortex_retrieval::bm25::{Bm25Ranker, Document};
use serde::Deserialize;
use std::collections::BTreeMap;

#[derive(Deserialize)]
struct Fixture {
    rank_bm25_version: String,
    sets: Vec<FixtureSet>,
}

#[derive(Deserialize)]
struct FixtureSet {
    name: String,
    documents: Vec<FixtureDoc>,
    cases: Vec<FixtureCase>,
}

#[derive(Deserialize)]
struct FixtureDoc {
    id: String,
    text: String,
}

#[derive(Deserialize)]
struct FixtureCase {
    query: String,
    expected: BTreeMap<String, f64>,
}

const TOLERANCE: f64 = 1e-9;

#[test]
fn golden_parity_against_python() {
    let raw = include_str!("fixtures/bm25_golden.json");
    let fixture: Fixture = serde_json::from_str(raw).expect("fixture JSON hợp lệ");
    assert_eq!(
        fixture.rank_bm25_version, "0.2.2",
        "fixtures phải sinh từ rank_bm25 0.2.2 — regenerate nếu đổi version"
    );

    for set in &fixture.sets {
        let mut ranker = Bm25Ranker::new();
        ranker.build_index(
            &set.documents
                .iter()
                .map(|d| Document::new(d.id.clone(), d.text.clone()))
                .collect::<Vec<_>>(),
        );
        for case in &set.cases {
            let actual = ranker.score(&case.query);
            let actual_keys: Vec<_> = actual.keys().collect();
            let expected_keys: Vec<_> = case.expected.keys().collect();
            assert_eq!(
                actual_keys, expected_keys,
                "set `{}` query `{}`: key set khác Python",
                set.name, case.query
            );
            for (sid, expected_score) in &case.expected {
                let diff = (actual[sid] - expected_score).abs();
                assert!(
                    diff <= TOLERANCE,
                    "set `{}` query `{}` symbol `{}`: rust={} python={} diff={diff}",
                    set.name,
                    case.query,
                    sid,
                    actual[sid],
                    expected_score
                );
            }
        }
    }
}

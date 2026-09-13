//! Golden parity test cho `signal_normalize` (nguồn: `signal_normalizer.py`).

use cortex_retrieval::signal_normalize::{
    batch_normalize, clamp, freshness_from_dirty_at, freshness_from_elapsed, min_max_normalize,
    normalize_signals,
};
use serde::Deserialize;
use std::collections::BTreeMap;

#[derive(Deserialize)]
struct ClampCase {
    value: f64,
    lo: f64,
    hi: f64,
    expected: f64,
}

#[derive(Deserialize)]
struct ValuesCase {
    values: Vec<f64>,
    expected: Vec<f64>,
}

#[derive(Deserialize)]
struct BatchCase {
    values: Vec<Option<f64>>,
    lo: Option<f64>,
    hi: Option<f64>,
    expected: Vec<f64>,
}

#[derive(Deserialize)]
struct SignalCase {
    raw: BTreeMap<String, f64>,
    bounds: Option<BTreeMap<String, (f64, f64)>>,
    expected: BTreeMap<String, f64>,
}

#[derive(Deserialize)]
struct FreshnessCase {
    elapsed: f64,
    half_life_days: f64,
    expected: f64,
}

#[derive(Deserialize)]
struct FreshnessDirty {
    now_epoch: f64,
    cases: Vec<DirtyCase>,
}

#[derive(Deserialize)]
struct DirtyCase {
    is_dirty: bool,
    iso: String,
    expected: f64,
}

#[derive(Deserialize)]
struct Fixture {
    clamp: Vec<ClampCase>,
    min_max: Vec<ValuesCase>,
    batch: Vec<BatchCase>,
    normalize_signals: Vec<SignalCase>,
    freshness_elapsed: Vec<FreshnessCase>,
    freshness_dirty: FreshnessDirty,
}

fn assert_close(a: f64, b: f64, context: &str) {
    assert!((a - b).abs() <= 1e-9, "{context}: rust={a} python={b}");
}

#[test]
fn golden_parity_against_python() {
    let fixture: Fixture =
        serde_json::from_str(include_str!("fixtures/signal_golden.json")).expect("fixture hợp lệ");

    for case in &fixture.clamp {
        assert_close(clamp(case.value, case.lo, case.hi), case.expected, "clamp");
    }
    for case in &fixture.min_max {
        let actual = min_max_normalize(&case.values);
        assert_eq!(actual.len(), case.expected.len(), "min_max len");
        for (a, b) in actual.iter().zip(&case.expected) {
            assert_close(*a, *b, "min_max");
        }
    }
    for case in &fixture.batch {
        let actual = batch_normalize(&case.values, case.lo, case.hi);
        assert_eq!(
            actual.len(),
            case.expected.len(),
            "batch len (values={:?})",
            case.values
        );
        for (a, b) in actual.iter().zip(&case.expected) {
            assert_close(*a, *b, "batch");
        }
    }
    for case in &fixture.normalize_signals {
        let bounds: Vec<(&str, (f64, f64))> = case
            .bounds
            .as_ref()
            .map(|b| b.iter().map(|(k, v)| (k.as_str(), *v)).collect())
            .unwrap_or_default();
        let raw: Vec<(&str, f64)> = case.raw.iter().map(|(k, v)| (k.as_str(), *v)).collect();
        let actual: BTreeMap<String, f64> = normalize_signals(&raw, &bounds).into_iter().collect();
        assert_eq!(actual.len(), case.expected.len(), "normalize_signals len");
        for (key, value) in &case.expected {
            assert_eq!(actual[key], *value, "normalize_signals[{key}]");
        }
    }
    for case in &fixture.freshness_elapsed {
        assert_close(
            freshness_from_elapsed(case.elapsed, case.half_life_days),
            case.expected,
            "freshness_elapsed",
        );
    }
    let dirty = &fixture.freshness_dirty;
    for case in &dirty.cases {
        assert_close(
            freshness_from_dirty_at(case.is_dirty, &case.iso, dirty.now_epoch),
            case.expected,
            &format!("freshness_dirty iso={}", case.iso),
        );
    }
}

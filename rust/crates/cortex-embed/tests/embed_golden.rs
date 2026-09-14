//! Gate parity code plane (phase-01): token id + vector của Rust ONNX embedder
//! phải khớp reference Python đã ghi trong fixture golden.
//!
//! Fixture sinh bởi `scripts/rust_parity/gen_embed_fixtures.py`. Test cần artifact
//! ONNX (vài GB) nên `#[ignore]` — `make rust-check` trên CI không có weights.
//! Chạy thật:
//!
//! ```text
//! make embed-artifacts
//! cargo test -p cortex-embed --manifest-path rust/Cargo.toml --test embed_golden -- --ignored
//! ```

use std::path::{Path, PathBuf};

use cortex_embed::{Embedder, ModelSpec, OnnxEmbedder, SessionConfig, hf_snapshot};
use serde::Deserialize;

const FIXTURE_PATH: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/jina_golden.json");
const COSINE_GATE: f32 = 0.999;

#[derive(Deserialize)]
struct Fixture {
    model: String,
    cases: Vec<Case>,
}

#[derive(Deserialize)]
struct Case {
    name: String,
    lane: String,
    text: String,
    truncated: bool,
    expected: Expected,
}

#[derive(Deserialize)]
struct Expected {
    ids: Vec<u32>,
    dimension: usize,
    vector: Vec<f32>,
}

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .map(PathBuf::from)
        .expect("rust/crates/<crate> must sit under the repo root")
}

fn base_spec(root: &Path, model: &str) -> ModelSpec {
    let graph = std::env::var("CORTEX_EMBED_GRAPH")
        .map(PathBuf::from)
        .unwrap_or_else(|_| root.join(".cache/embed/jina-v3-onnx-fp32/model.onnx"));
    let snapshot = hf_snapshot(model)
        .unwrap_or_else(|| panic!("{model}: no cached HF snapshot — run `make embed-artifacts`"));
    let spec = ModelSpec::jina_v3(&snapshot, graph);
    spec.validate().expect("jina-v3 artifacts present");
    spec
}

fn load_fixture() -> Fixture {
    let raw = std::fs::read_to_string(FIXTURE_PATH)
        .unwrap_or_else(|error| panic!("{}: {error} — run gen_embed_fixtures.py", FIXTURE_PATH));
    serde_json::from_str(&raw).expect("fixture json")
}

fn cosine(a: &[f32], b: &[f32]) -> f32 {
    if a.len() != b.len() {
        return -1.0;
    }
    let dot: f32 = a.iter().zip(b).map(|(x, y)| x * y).sum();
    let left: f32 = a.iter().map(|x| x * x).sum::<f32>().sqrt();
    let right: f32 = b.iter().map(|x| x * x).sum::<f32>().sqrt();
    if left == 0.0 || right == 0.0 {
        return -1.0;
    }
    dot / (left * right)
}

/// Hai lane của cùng một graph, khác nhau ở tiền xử lý text (findings C1).
struct Lanes {
    ingest: OnnxEmbedder,
    query: OnnxEmbedder,
}

impl Lanes {
    fn new(root: &Path, model: &str) -> Self {
        let spec = base_spec(root, model);
        Self {
            ingest: OnnxEmbedder::new(spec.clone()).expect("ingest embedder"),
            query: OnnxEmbedder::new(spec.query_lane()).expect("query embedder"),
        }
    }

    fn get(&self, lane: &str) -> &OnnxEmbedder {
        match lane {
            "ingest" => &self.ingest,
            "query" => &self.query,
            other => panic!("unknown lane {other:?} in fixture"),
        }
    }
}

#[test]
#[ignore = "requires ONNX artifacts; run `make embed-artifacts` first"]
fn token_ids_match_python() {
    let fixture = load_fixture();
    let lanes = Lanes::new(&repo_root(), &fixture.model);

    let mut checked = 0usize;
    let mut drift = Vec::new();
    for lane in ["ingest", "query"] {
        let cases: Vec<&Case> = fixture
            .cases
            .iter()
            .filter(|case| case.lane == lane && !case.truncated)
            .collect();
        let texts: Vec<String> = cases.iter().map(|case| case.text.clone()).collect();
        let got = lanes
            .get(lane)
            .tokenize_ids(&texts)
            .expect("tokenize must succeed");
        for (case, ids) in cases.iter().zip(&got) {
            checked += 1;
            if *ids != case.expected.ids {
                drift.push(format!(
                    "{}: rust {} ids vs python {} ids (first diff {:?})",
                    case.name,
                    ids.len(),
                    case.expected.ids.len(),
                    ids.iter()
                        .zip(&case.expected.ids)
                        .position(|(a, b)| a != b)
                        .map(|index| (index, ids[index], case.expected.ids[index]))
                ));
            }
        }
    }
    println!("[gate] token-id checked={checked} drift={}", drift.len());
    assert!(checked > 0, "fixture has no usable cases");
    assert!(drift.is_empty(), "token-id drift: {drift:?}");
}

#[test]
#[ignore = "requires ONNX artifacts; run `make embed-artifacts` first"]
fn throughput_probe() {
    // Số liệu cho gate benchmark phase-01 / decision P05. Python reference chạy ở
    // `scripts/rust_parity/bench_embed_python.py` với cùng corpus + cùng 4 threads.
    let fixture = load_fixture();
    let texts: Vec<String> = fixture
        .cases
        .iter()
        .filter(|case| case.lane == "ingest")
        .map(|case| case.text.clone())
        .collect();

    for batch in [8usize, 32, 128] {
        let spec = base_spec(&repo_root(), &fixture.model);
        let embedder = OnnxEmbedder::with_config(spec, SessionConfig::default())
            .expect("embedder")
            .with_batch(batch);
        // warm-up: model load + first allocation không tính vào số đo.
        let _ = embedder.embed(&texts[..2]).expect("warm-up embed");
        let started = std::time::Instant::now();
        let vectors = embedder.embed(&texts).expect("bench embed");
        let seconds = started.elapsed().as_secs_f64();
        println!(
            "[bench] ort batch={batch:3} texts={} time={seconds:.2}s throughput={:.1} texts/s",
            vectors.len(),
            vectors.len() as f64 / seconds
        );
    }

    // Latency single-text (warm) — số so với `bench_embed_python.py` p50/p95.
    let cold = std::time::Instant::now();
    let embedder = OnnxEmbedder::with_config(
        base_spec(&repo_root(), &fixture.model),
        SessionConfig::default(),
    )
    .expect("latency embedder");
    println!(
        "[bench] ort cold start (tokenizer+session load): {:.2}s",
        cold.elapsed().as_secs_f64()
    );
    let mut latencies: Vec<f64> = Vec::new();
    for text in texts.iter().take(40) {
        let started = std::time::Instant::now();
        let one = embedder.embed(&[text.clone()]).expect("latency embed");
        assert_eq!(one.len(), 1);
        latencies.push(started.elapsed().as_secs_f64() * 1000.0);
    }
    latencies.sort_by(|a, b| a.total_cmp(b));
    println!(
        "[bench] ort single-text p50={:.1}ms p95={:.1}ms n={} threads={}",
        latencies[latencies.len() / 2],
        latencies[(latencies.len() as f64 * 0.95) as usize - 1],
        latencies.len(),
        SessionConfig::default().intra_threads
    );
}

#[test]
#[ignore = "requires ONNX artifacts; run `make embed-artifacts` first"]
fn vectors_match_python() {
    let fixture = load_fixture();
    let lanes = Lanes::new(&repo_root(), &fixture.model);

    let mut checked = 0usize;
    let mut sum = 0f64;
    let mut worst = (f32::MAX, String::from("<none>"));
    let mut failures = Vec::new();

    for lane in ["ingest", "query"] {
        let cases: Vec<&Case> = fixture.cases.iter().filter(|case| case.lane == lane).collect();
        let texts: Vec<String> = cases.iter().map(|case| case.text.clone()).collect();
        let embedder = lanes.get(lane);
        let vectors = embedder.embed(&texts).expect("embed must succeed");
        assert_eq!(vectors.len(), cases.len(), "{lane}: vector count");
        for (case, vector) in cases.iter().zip(&vectors) {
            checked += 1;
            assert_eq!(vector.len(), case.expected.dimension, "{}: dimension", case.name);
            let value = cosine(vector, &case.expected.vector);
            sum += f64::from(value);
            if value < worst.0 {
                worst = (value, case.name.clone());
            }
            if value < COSINE_GATE {
                failures.push(format!("{}: cosine {value:.6}", case.name));
            }
        }
    }

    println!(
        "[gate] cosine cases={checked} mean={:.7} worst={:.7} ({}) below_gate={}",
        sum / checked.max(1) as f64,
        worst.0,
        worst.1,
        failures.len()
    );
    assert!(
        failures.is_empty(),
        "{} cases below gate {COSINE_GATE}: {:?}",
        failures.len(),
        failures
    );
}

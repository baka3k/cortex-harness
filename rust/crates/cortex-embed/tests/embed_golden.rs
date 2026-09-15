//! Gate parity embedding (phase-01 code plane + phase-02 doc plane): token id và
//! vector của Rust ONNX embedder phải khớp reference Python trong fixture golden.
//!
//! Một harness cho mọi plane: mỗi file trong `tests/fixtures/*_golden.json` tự mô
//! tả plane/model/graph của nó, nên thêm model mới chỉ cần thêm fixture, không cần
//! thêm test. Fixture sinh bởi `scripts/rust_parity/gen_embed_fixtures.py`.
//!
//! Test cần artifact ONNX (mỗi graph ~2.2GB) nên `#[ignore]` — `make rust-check`
//! trên CI không có weights. Chạy thật:
//!
//! ```text
//! make embed-artifacts
//! make embed-parity
//! ```

use std::path::{Path, PathBuf};

use cortex_embed::{Embedder, ModelSpec, OnnxEmbedder, SessionConfig, hf_snapshot};
use serde::Deserialize;

const FIXTURE_DIR: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures");
const COSINE_GATE: f32 = 0.999;

#[derive(Deserialize)]
struct Fixture {
    plane: String,
    model: String,
    graph: Option<String>,
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

/// Spec theo plane đã đối chiếu ở `findings.md`: code = mean+normalize (jina-v3),
/// doc = CLS+normalize (bge-m3). Graph đọc từ chính fixture để không lệch artifact.
fn spec_for(root: &Path, fixture: &Fixture) -> ModelSpec {
    let graph = std::env::var("CORTEX_EMBED_GRAPH")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            let relative = fixture
                .graph
                .as_deref()
                .unwrap_or(".cache/embed/UNSET/model.onnx");
            root.join(relative)
        });
    let snapshot = hf_snapshot(&fixture.model)
        .unwrap_or_else(|| panic!("{}: no cached HF snapshot — run `make embed-artifacts`", fixture.model));
    let spec = match fixture.plane.as_str() {
        "code" => ModelSpec::jina_v3(&snapshot, graph),
        "doc" => ModelSpec::bge_m3(&snapshot, graph),
        other => panic!("fixture {} declares unknown plane {other:?}", fixture.plane),
    };
    spec.validate()
        .unwrap_or_else(|error| panic!("{}: {error}", fixture.model));
    spec
}

/// Hai lane của plane code dùng cùng graph nhưng khác tiền xử lý text; plane doc
/// chỉ có một hành vi (worker và ingest đều đi qua SentenceTransformer).
struct Lanes {
    ingest: OnnxEmbedder,
    query: OnnxEmbedder,
}

impl Lanes {
    fn new(root: &Path, fixture: &Fixture) -> Self {
        let spec = spec_for(root, fixture);
        let query_spec = match fixture.plane.as_str() {
            "code" => spec.clone().query_lane(),
            _ => spec.clone(),
        };
        Self {
            ingest: OnnxEmbedder::with_config(spec, SessionConfig::default()).expect("ingest embedder"),
            query: OnnxEmbedder::with_config(query_spec, SessionConfig::default())
                .expect("query embedder"),
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

fn fixtures() -> Vec<(PathBuf, Fixture)> {
    let dir = Path::new(FIXTURE_DIR);
    let mut found: Vec<PathBuf> = dir
        .read_dir()
        .expect("fixtures dir")
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.extension().map(|ext| ext == "json").unwrap_or(false)
                && path
                    .file_name()
                    .map(|name| name.to_string_lossy().ends_with("_golden.json"))
                    .unwrap_or(false)
        })
        .collect();
    found.sort();
    assert!(!found.is_empty(), "no *_golden.json fixture in {}", dir.display());
    found
        .into_iter()
        .map(|path| {
            let raw = std::fs::read_to_string(&path)
                .unwrap_or_else(|error| panic!("{}: {error}", path.display()));
            let parsed: Fixture =
                serde_json::from_str(&raw).unwrap_or_else(|error| panic!("{}: {error}", path.display()));
            (path, parsed)
        })
        .collect()
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

#[test]
#[ignore = "requires ONNX artifacts; run `make embed-artifacts` first"]
fn token_ids_match_python() {
    let root = repo_root();
    let mut checked = 0usize;
    let mut drift = Vec::new();

    for (path, fixture) in fixtures() {
        let lanes = Lanes::new(&root, &fixture);
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
                .unwrap_or_else(|error| panic!("{} tokenize: {error}", fixture.model));
            for (case, ids) in cases.iter().zip(&got) {
                checked += 1;
                if *ids != case.expected.ids {
                    drift.push(format!(
                        "{} {}:{} rust {} ids vs python {} ids (first diff {:?})",
                        fixture.plane,
                        path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
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
    }
    println!("[gate] token-id checked={checked} drift={}", drift.len());
    assert!(checked > 0, "fixtures contain no usable cases");
    assert!(drift.is_empty(), "token-id drift: {drift:?}");
}

#[test]
#[ignore = "requires ONNX artifacts; run `make embed-artifacts` first"]
fn vectors_match_python() {
    let root = repo_root();
    let mut checked = 0usize;
    let mut sum = 0f64;
    let mut worst = (f32::MAX, String::from("<none>"));
    let mut failures = Vec::new();

    for (_path, fixture) in fixtures() {
        let lanes = Lanes::new(&root, &fixture);
        for lane in ["ingest", "query"] {
            let cases: Vec<&Case> = fixture.cases.iter().filter(|case| case.lane == lane).collect();
            if cases.is_empty() {
                continue;
            }
            let texts: Vec<String> = cases.iter().map(|case| case.text.clone()).collect();
            let vectors = lanes
                .get(lane)
                .embed(&texts)
                .unwrap_or_else(|error| panic!("{} embed: {error}", fixture.model));
            assert_eq!(vectors.len(), cases.len(), "{}: vector count", fixture.plane);
            for (case, vector) in cases.iter().zip(&vectors) {
                checked += 1;
                assert_eq!(
                    vector.len(),
                    case.expected.dimension,
                    "{}: dimension drift",
                    case.name
                );
                let value = cosine(vector, &case.expected.vector);
                sum += f64::from(value);
                if value < worst.0 {
                    worst = (value, format!("{} {}", fixture.plane, case.name));
                }
                if value < COSINE_GATE {
                    failures.push(format!("{} {}: cosine {value:.6}", fixture.plane, case.name));
                }
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

#[test]
#[ignore = "requires ONNX artifacts; run `make embed-artifacts` first"]
fn throughput_probe() {
    // Số liệu cho gate benchmark phase-01, gate P95/throughput phase-02 và quyết
    // định P05. Phía Python tương ứng: `bench_embed_python.py` (code plane) và
    // `bench_mind_worker.py` (đường sidecar persistent của mind tools).
    let root = repo_root();
    for (_path, fixture) in fixtures() {
        let plane = fixture.plane.clone();
        let texts: Vec<String> = fixture
            .cases
            .iter()
            .filter(|case| case.lane == "ingest")
            .map(|case| case.text.clone())
            .collect();
        let spec = spec_for(&root, &fixture);

        for batch in [8usize, 32, 128] {
            let embedder =
                OnnxEmbedder::with_config(spec.clone(), SessionConfig::default())
                    .expect("embedder")
                    .with_batch(batch);
            // warm-up: load + alloc lần đầu không tính vào số đo.
            let _ = embedder.embed(&texts[..2]).expect("warm-up embed");
            let started = std::time::Instant::now();
            let vectors = embedder.embed(&texts).expect("bench embed");
            let seconds = started.elapsed().as_secs_f64();
            println!(
                "[bench:{plane}] ort batch={batch:3} texts={} time={seconds:.2}s throughput={:.1} texts/s",
                vectors.len(),
                vectors.len() as f64 / seconds
            );
        }

        // Latency single-text (warm) — số so với p50/p95 của phía Python.
        // Porta dùng text của lane *query* chứ không phải paragraph ingest: gate
        // "P95 query-embed (mind tools)" đo trên query thật (~20-40 ký tự), còn
        // paragraph 500 ký tự sẽ phóng đại cả hai phía và làm lệch kết luận.
        let query_texts: Vec<String> = fixture
            .cases
            .iter()
            .filter(|case| case.lane == "query")
            .map(|case| case.text.clone())
            .collect();
        let latency_corpus: Vec<String> = if query_texts.is_empty() {
            texts.iter().take(40).cloned().collect()
        } else {
            query_texts.iter().cycle().take(48).cloned().collect()
        };
        let cold = std::time::Instant::now();
        let embedder =
            OnnxEmbedder::with_config(spec, SessionConfig::default()).expect("latency embedder");
        println!(
            "[bench:{plane}] ort cold start (tokenizer+session load): {:.2}s",
            cold.elapsed().as_secs_f64()
        );
        let mut latencies: Vec<f64> = Vec::new();
        for text in &latency_corpus {
            let started = std::time::Instant::now();
            let one = embedder.embed(std::slice::from_ref(text)).expect("latency embed");
            assert_eq!(one.len(), 1);
            latencies.push(started.elapsed().as_secs_f64() * 1000.0);
        }
        latencies.sort_by(|a, b| a.total_cmp(b));
        let config = SessionConfig::default();
        println!(
            "[bench:{plane}] ort query-text p50={:.1}ms p95={:.1}ms n={} threads={} deterministic={}",
            latencies[latencies.len() / 2],
            latencies[(latencies.len() as f64 * 0.95) as usize - 1],
            latencies.len(),
            config.intra_threads,
            config.deterministic
        );
    }
}

/// Phase-06 G7 build/repro gate: every on-disk artifact the parity fixtures
/// were measured against must still match the digests pinned in-repo
/// (`cortex_embed::model_pin`). This is the "exported graph sha256 ghi
/// in-repo" half that cannot run inside `verify_provenance` — the weights file
/// is 2.2 GB and hashing it per process load would stall sync.
#[test]
#[ignore]
fn provenance_pins_match_disk() {
    let root = repo_root();
    let mut checked = 0usize;
    for (path, fixture) in fixtures() {
        let Some(pin) = cortex_embed::model_pin(&fixture.model) else {
            println!("[provenance] {}: no in-repo pin (custom model) — skipped", path.display());
            continue;
        };
        let spec = spec_for(&root, &fixture);
        let graph_digest = cortex_embed::file_sha256(&spec.graph).expect("graph readable");
        assert_eq!(
            graph_digest, pin.graph_sha256,
            "{}: exported graph digest diverged from the in-repo pin",
            fixture.model
        );
        let weights = spec.graph.with_file_name(pin.weights_file);
        if weights.is_file() {
            let weights_digest =
                cortex_embed::file_sha256(&weights).expect("weights readable");
            assert_eq!(
                weights_digest, pin.weights_sha256,
                "{}: weights digest diverged",
                fixture.model
            );
        }
        println!(
            "[provenance] {} graph+weights match pin (hf revision {})",
            fixture.model, pin.hf_revision
        );
        checked += 1;
    }
    assert!(checked > 0, "no pinned model fixture found");
}

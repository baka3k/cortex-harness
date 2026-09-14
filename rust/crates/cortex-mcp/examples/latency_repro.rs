//! Throwaway repro (phase-02 mục 3b): stall ~45ms của request qdrant chỉ xuất
//! hiện trong MCP server thật — không tái hiện được bằng process thuần. Biến
//! số còn lại: blocking ureq + ORT inference chạy TRÊN tokio worker thread.
//!
//! `cargo run -p cortex-mcp --example latency_repro`

use cortex_embed::{Embedder as _, OnnxEmbedder, Plane, spec_from_env};
use std::time::{Duration, Instant};

const QDRANT_SEARCH: &str = "http://127.0.0.1:6333/collections/mindfix_doc/points/search";
const QDRANT_LIST: &str = "http://127.0.0.1:6333/collections";

fn embed_once(embedder: &OnnxEmbedder) -> f64 {
    let started = Instant::now();
    embedder
        .embed(&["tokio repro query".to_string()])
        .expect("embed");
    started.elapsed().as_secs_f64() * 1000.0
}

fn ureq_get_pooled(agent: &ureq::Agent) -> f64 {
    let started = Instant::now();
    let response = agent.get(QDRANT_LIST).call().expect("qdrant get");
    let mut raw = String::new();
    use std::io::Read as _;
    response.into_reader().read_to_string(&mut raw).expect("read");
    started.elapsed().as_secs_f64() * 1000.0
}

fn ureq_post_pooled(agent: &ureq::Agent) -> f64 {
    let body = serde_json::json!({ "vector": vec![0.1f32; 1024], "limit": 5 });
    let started = Instant::now();
    let response = agent
        .post(QDRANT_SEARCH)
        .set("Content-Type", "application/json")
        .send_json(body)
        .expect("qdrant post");
    let mut raw = String::new();
    use std::io::Read as _;
    response.into_reader().read_to_string(&mut raw).expect("read");
    started.elapsed().as_secs_f64() * 1000.0
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let workers = std::thread::available_parallelism().map(|w| w.get()).unwrap_or(4);
    println!("tokio multi-thread runtime, workers={workers}");
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;

    let spec = spec_from_env(Plane::Doc)?;
    let embedder = std::sync::Arc::new(OnnxEmbedder::new(spec)?);
    let agent = std::sync::Arc::new(
        ureq::AgentBuilder::new()
            .timeout_connect(Duration::from_secs(30))
            .timeout(Duration::from_secs(30))
            .build(),
    );

    // V1 — đúng hình của rmcp handler: embed + blocking ureq trên worker.
    // Trình tự qdrant y hệt server: GET /collections rồi POST /points/search,
    // chung một agent (pool).
    runtime.block_on(async {
        let embedder = embedder.clone();
        let agent = agent.clone();
        let task = tokio::spawn(async move {
            // warmup: model load + pool connect
            let _ = embed_once(&embedder);
            let _ = ureq_get_pooled(&agent);
            let _ = ureq_post_pooled(&agent);
            for round in 0..5 {
                let embed_ms = embed_once(&embedder);
                let get_ms = ureq_get_pooled(&agent);
                let post_ms = ureq_post_pooled(&agent);
                println!(
                    "V1 embed+get+post       round {round}: embed={embed_ms:.1}ms \
                     get={get_ms:.1}ms post={post_ms:.1}ms"
                );
            }
        });
        task.await.expect("V1 task");
    });

    // V3 — GET+POST KHÔNG embed, chỉ sleep 20ms: tách "khoảng trống thời gian"
    // khỏi "đốt CPU thật".
    runtime.block_on(async {
        let agent = agent.clone();
        let task = tokio::spawn(async move {
            for round in 0..5 {
                std::thread::sleep(Duration::from_millis(20));
                let get_ms = ureq_get_pooled(&agent);
                let post_ms = ureq_post_pooled(&agent);
                println!(
                    "V3 sleep20+get+post     round {round}: get={get_ms:.1}ms \
                     post={post_ms:.1}ms"
                );
            }
        });
        task.await.expect("V3 task");
    });

    // V4 — GET+POST KHÔNG embed, sleep 60ms: đối chiếu với probe keep-alive cũ.
    runtime.block_on(async {
        let agent = agent.clone();
        let task = tokio::spawn(async move {
            for round in 0..5 {
                std::thread::sleep(Duration::from_millis(60));
                let get_ms = ureq_get_pooled(&agent);
                let post_ms = ureq_post_pooled(&agent);
                println!(
                    "V4 sleep60+get+post     round {round}: get={get_ms:.1}ms \
                     post={post_ms:.1}ms"
                );
            }
        });
        task.await.expect("V4 task");
    });

    // V5 — chỉ POST sau sleep 20ms (không GET): nếu POST cũng dính stall thì
    // cache GET không cứu được tổng; nếu không, cache GET là fix.
    runtime.block_on(async {
        let agent = agent.clone();
        let task = tokio::spawn(async move {
            for round in 0..5 {
                std::thread::sleep(Duration::from_millis(20));
                let post_ms = ureq_post_pooled(&agent);
                println!("V5 sleep20+post-only    round {round}: post={post_ms:.1}ms");
            }
        });
        task.await.expect("V5 task");
    });

    // V6 — GET với Connection: close sau sleep 20ms: connection mới mỗi lần
    // có né được stall của keep-alive reuse không?
    runtime.block_on(async {
        let agent = agent.clone();
        let task = tokio::spawn(async move {
            for round in 0..5 {
                std::thread::sleep(Duration::from_millis(20));
                let started = Instant::now();
                let response = agent
                    .get(QDRANT_LIST)
                    .set("Connection", "close")
                    .call()
                    .expect("qdrant get close");
                let mut raw = String::new();
                use std::io::Read as _;
                response
                    .into_reader()
                    .read_to_string(&mut raw)
                    .expect("read");
                println!(
                    "V6 sleep20+get-fresh    round {round}: get={:.1}ms",
                    started.elapsed().as_secs_f64() * 1000.0
                );
            }
        });
        task.await.expect("V6 task");
    });

    // V2 — embed + ureq đều qua spawn_blocking (không đụng worker).
    runtime.block_on(async {
        let embedder = embedder.clone();
        let agent = agent.clone();
        let task = tokio::spawn(async move {
            for round in 0..5 {
                let embedder = embedder.clone();
                let agent = agent.clone();
                let (embed_ms, qdrant_ms) = tokio::task::spawn_blocking(move || {
                    let embed_ms = embed_once(&embedder);
                    let qdrant_ms = ureq_post_pooled(&agent);
                    (embed_ms, qdrant_ms)
                })
                .await
                .expect("blocking join");
                println!("V2 spawn_blocking      round {round}: embed={embed_ms:.1}ms qdrant={qdrant_ms:.1}ms");
            }
        });
        task.await.expect("V2 task");
    });

    Ok(())
}

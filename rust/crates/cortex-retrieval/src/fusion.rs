//! Port của fusion pipeline trong `code-tiny/tools/common/intelligent_retrieval.py`
//! (merge seeds → BM25 injection → freshness → batch normalize → weighted score → top_k).
//!
//! Phần network IO thật (Qdrant search, graph keyword search, graph expansion)
//! **không port ở phase này** — seeds sau khi convert đứng trong `SeedInputs`,
//! caller (Python sidecar / binary sau này) chịu trách nhiệm lấy chúng.
//! Golden fixtures: `tests/fixtures/fusion_golden.json`.

use std::collections::{BTreeMap, BTreeSet, HashMap};

use serde::Deserialize;
use serde_json::Value;

use crate::bm25::Bm25Ranker;
use crate::intent;
use crate::signal_normalize::{freshness_from_dirty_at, min_max_normalize};

/// Raw Qdrant hit — tương đương `{"id", "score", "payload"}`.
#[derive(Debug, Clone, Deserialize)]
pub struct QdrantHit {
    pub id: Value,
    pub score: f64,
    #[serde(default)]
    pub payload: BTreeMap<String, Value>,
}

fn map_str(map: &BTreeMap<String, Value>, key: &str) -> String {
    map.get(key).and_then(Value::as_str).unwrap_or("").to_string()
}

fn map_f64(map: &BTreeMap<String, Value>, key: &str) -> f64 {
    map.get(key).and_then(Value::as_f64).unwrap_or(0.0)
}

fn map_bool(map: &BTreeMap<String, Value>, key: &str) -> bool {
    map.get(key).and_then(Value::as_bool).unwrap_or(false)
}

fn value_str(value: &Value) -> String {
    value.as_str().unwrap_or("").to_string()
}

/// Port `_qdrant_hit_to_candidate` — hit Qdrant → candidate phẳng.
/// Lưu ý: Python `str(x or "")` wrap mọi type sang string; ở đây chỉ hỗ trợ
/// string payload (fixture chỉ dùng string — đủ cho schema hiện tại).
pub fn candidate_from_qdrant_hit(hit: &QdrantHit) -> Candidate {
    let payload = &hit.payload;
    let node_id = payload
        .get("symbol_id")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map_or_else(
            || match &hit.id {
                Value::String(s) => s.clone(),
                _ => String::new(),
            },
            String::from,
        );
    let usage = payload
        .get("signals")
        .and_then(Value::as_object)
        .and_then(|s| s.get("usage"))
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    Candidate {
        node_id,
        name: map_str(payload, "name"),
        qualified_name: map_str(payload, "qualified_name"),
        kind: map_str(payload, "kind"),
        file_path: map_str(payload, "file_path"),
        semantic: hit.score,
        keyword: 0.0,
        graph: 0.0,
        freshness: 0.0,
        confidence: map_f64(payload, "doc_confidence"),
        usage,
        bm25: 0.0,
        intent: map_str(payload, "intent"),
        exported: map_bool(payload, "exported"),
        side_effect: map_bool(payload, "side_effect"),
        return_type: map_str(payload, "return_type"),
        project_id: map_str(payload, "project_id"),
        language: map_str(payload, "language"),
        source: "qdrant".to_string(),
    }
}

/// Port `_graph_keyword_node_to_candidate` — node graph keyword → candidate.
pub fn candidate_from_keyword_node(node: &BTreeMap<String, Value>) -> Candidate {
    let node_id = node.get("id").map(value_str).unwrap_or_default();
    Candidate {
        node_id,
        name: map_str(node, "name"),
        qualified_name: map_str(node, "qualified_name"),
        kind: map_str(node, "kind"),
        file_path: map_str(node, "file_path"),
        semantic: 0.0,
        keyword: 1.0,
        graph: 0.0,
        freshness: 0.0,
        confidence: map_f64(node, "doc_confidence"),
        usage: 0.0,
        bm25: 0.0,
        intent: map_str(node, "intent"),
        exported: map_bool(node, "exported"),
        side_effect: map_bool(node, "side_effect"),
        return_type: map_str(node, "return_type"),
        project_id: map_str(node, "project_id"),
        language: map_str(node, "language"),
        source: "graph_keyword".to_string(),
    }
}

/// Thứ tự signal cố định của `_SIGNAL_KEYS` — quyết định thứ tự cộng float.
pub const SIGNAL_KEYS: [&str; 6] = [
    "semantic",
    "keyword",
    "graph",
    "freshness",
    "confidence",
    "usage",
];

/// Candidate phẳng — tương đương dict candidate của Python.
/// `serde(default)` container-level để parse được candidate tối thiểu
/// `{"node_id", "bm25"}` mà Python BM25 injection tạo ra.
#[derive(Debug, Clone, PartialEq, Default, serde::Serialize, serde::Deserialize)]
#[serde(default)]
pub struct Candidate {
    pub node_id: String,
    pub name: String,
    pub qualified_name: String,
    pub kind: String,
    pub file_path: String,
    pub semantic: f64,
    pub keyword: f64,
    pub graph: f64,
    pub freshness: f64,
    pub confidence: f64,
    pub usage: f64,
    pub bm25: f64,
    pub intent: String,
    pub exported: bool,
    pub side_effect: bool,
    pub return_type: String,
    pub project_id: String,
    pub language: String,
    #[serde(rename = "_source")]
    pub source: String,
}

/// Seeds đã convert (bên Python là output của `_retrieve_qdrant`/`_retrieve_keyword`).
#[derive(Debug, Clone, Default)]
pub struct SeedInputs {
    pub qdrant: Vec<Candidate>,
    pub keyword: Vec<Candidate>,
}

/// Tương đương `ScoredResult`.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct ScoredResult {
    pub node_id: String,
    pub score: f64,
    pub node: Candidate,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub explanation: Option<Explanation>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Explanation {
    pub raw_signals: BTreeMap<String, f64>,
    pub weighted_contributions: BTreeMap<String, f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub query_intent: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub weights_used: Option<BTreeMap<String, f64>>,
}

/// Weights dạng ordered Vec — replicate Python dict insertion order (quyết định
/// thứ tự cộng float trong `sum(weights.values())` và `weighted_sum`).
type Weights = Vec<(String, f64)>;

fn profile_weights(intent: &str) -> Weights {
    intent::get_weight_profile(intent)
        .into_iter()
        .map(|(k, v)| (k.to_string(), v))
        .collect()
}

fn apply_override(mut weights: Weights, overrides: &[(&str, f64)]) -> Weights {
    for (key, value) in overrides {
        if let Some(entry) = weights.iter_mut().find(|(k, _)| k == key) {
            entry.1 = *value;
        } else {
            weights.push(((*key).to_string(), *value));
        }
    }
    weights
}

/// `_normalize_weights` — total tính theo insertion order; total ≤ 0 → 1/n.
fn normalize_weights(weights: &Weights) -> Weights {
    let total: f64 = weights.iter().map(|(_, v)| v).sum();
    if total <= 0.0 {
        let equal = 1.0 / SIGNAL_KEYS.len() as f64;
        return SIGNAL_KEYS
            .iter()
            .map(|k| ((*k).to_string(), equal))
            .collect();
    }
    weights
        .iter()
        .map(|(k, v)| (k.clone(), v / total))
        .collect()
}

fn round6(value: f64) -> f64 {
    format!("{value:.6}").parse().unwrap_or(value)
}

/// Fusion engine — tương đương `IntelligentRetrievalEngine` (offline core).
#[derive(Default)]
pub struct FusionEngine {
    /// node_id → last_updated ISO (như `freshness_map`).
    pub freshness_map: BTreeMap<String, String>,
    /// node_id đang dirty (như `dirty_set`).
    pub dirty_set: BTreeSet<String>,
    /// BM25 ranker đã build index (như `bm25_ranker`).
    pub bm25_ranker: Option<Bm25Ranker>,
    pub bm25_weight: f64,
}

/// Insert candidate mới giữ insertion order — bỏ qua node_id rỗng/trùng.
fn upsert_new(
    order: &mut Vec<String>,
    candidates: &mut HashMap<String, Candidate>,
    c: Candidate,
) {
    let nid = c.node_id.clone();
    if nid.is_empty() || candidates.contains_key(&nid) {
        return;
    }
    order.push(nid.clone());
    candidates.insert(nid, c);
}

impl FusionEngine {
    pub fn bm25_weight_default() -> f64 {
        0.15
    }

    /// Tương đương `search(...)` với seeds đã lấy sẵn; `expand_graph` chưa port
    /// (phase sau, cần GraphExpander). `now_epoch_s` cho freshness decay —
    /// bên Python dùng `datetime.now()` nội tâm, ở đây truyền vào để test được.
    pub fn search(
        &self,
        query: &str,
        seeds: &SeedInputs,
        top_k: usize,
        debug: bool,
        weight_override: &[(&str, f64)],
        now_epoch_s: f64,
    ) -> Vec<ScoredResult> {
        let q = query.trim();
        if q.is_empty() {
            return Vec::new();
        }

        // 1. Classify → weight profile (+ override). `merged_weights` giữ lại
        //    cho explanation["weights_used"] — Python gắn weights TRƯỚC khi
        //    thêm bm25 và TRƯỚC khi normalize (đúng vị trí dict(weights)).
        let intent = intent::classify_query(q);
        let merged_weights = apply_override(profile_weights(intent), weight_override);
        let mut weights = merged_weights.clone();

        // 2. Merge candidates — Python dict: qdrant trước, keyword sau;
        //    keyword trùng node_id → keyword = max(existing, 1.0).
        let mut order: Vec<String> = Vec::new();
        let mut candidates: HashMap<String, Candidate> = HashMap::new();
        for c in seeds.qdrant.clone() {
            upsert_new(&mut order, &mut candidates, c);
        }
        for c in seeds.keyword.clone() {
            let nid = c.node_id.clone();
            if nid.is_empty() {
                continue;
            }
            match candidates.get_mut(&nid) {
                Some(existing) => existing.keyword = existing.keyword.max(1.0),
                None => upsert_new(&mut order, &mut candidates, c),
            }
        }

        // 2b. BM25 injection — candidate đã có → gắn score; chưa có → thêm
        //     candidate tối thiểu (Python chỉ làm khi không có project filter,
        //     phase này chưa port project scope nên luôn như vậy).
        if let Some(ranker) = &self.bm25_ranker {
            for (nid, score) in ranker.score(q) {
                match candidates.get_mut(&nid) {
                    Some(existing) => existing.bm25 = score,
                    None => {
                        order.push(nid.clone());
                        candidates.insert(
                            nid.clone(),
                            Candidate {
                                node_id: nid,
                                bm25: score,
                                ..Default::default()
                            },
                        );
                    }
                }
            }
        }

        // 4. Freshness inject
        for nid in &order {
            let c = candidates.get_mut(nid).expect("vừa insert");
            let is_dirty = self.dirty_set.contains(nid);
            let last_updated = self.freshness_map.get(nid).cloned().unwrap_or_default();
            c.freshness = freshness_from_dirty_at(is_dirty, &last_updated, now_epoch_s);
        }

        // 4b. Batch normalize — semantic/keyword min-max (round6), còn lại clamp.
        let sem_vals: Vec<f64> = order.iter().map(|nid| candidates[nid].semantic).collect();
        let kw_vals: Vec<f64> = order.iter().map(|nid| candidates[nid].keyword).collect();
        let sem_normed = min_max_normalize(&sem_vals);
        let kw_normed = min_max_normalize(&kw_vals);
        let mut candidate_list: Vec<Candidate> = Vec::with_capacity(order.len());
        for (nid, (s, k)) in order.iter().zip(sem_normed.into_iter().zip(kw_normed)) {
            let mut c = candidates.remove(nid).expect("vừa insert");
            c.semantic = round6(s);
            c.keyword = round6(k);
            c.graph = c.graph.clamp(0.0, 1.0);
            c.freshness = c.freshness.clamp(0.0, 1.0);
            c.confidence = c.confidence.clamp(0.0, 1.0);
            c.usage = c.usage.clamp(0.0, 1.0);
            c.bm25 = c.bm25.clamp(0.0, 1.0);
            candidate_list.push(c);
        }

        // 5. Score — Python fix 2026-09-13: khi weight "bm25" tồn tại, giá trị
        //    bm25 của candidate được cộng vào weighted sum (trước fix nó chỉ
        //    pha loãng denominator — xem phase-04). Rust replicate behavior
        //    sau fix; golden fixture sinh từ Python sau fix.
        if self.bm25_ranker.is_some() && candidate_list.iter().any(|c| c.bm25 > 0.0) {
            weights = apply_override(weights, &[("bm25", self.bm25_weight)]);
        }
        let weights = normalize_weights(&weights);
        let mut results: Vec<ScoredResult> = candidate_list
            .into_iter()
            .map(|c| score_candidate(&c, &weights, debug))
            .collect();

        // 6. Rank desc (stable — tie giữ thứ tự candidate như Python) + top_k.
        results.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
        results.truncate(top_k);

        if debug {
            for r in &mut results {
                if let Some(expl) = &mut r.explanation {
                    expl.query_intent = Some(intent);
                    expl.weights_used = Some(merged_weights.iter().cloned().collect());
                }
            }
        }
        results
    }
}

fn score_candidate(candidate: &Candidate, weights: &Weights, debug: bool) -> ScoredResult {
    let mut raw_signals: Vec<(&str, f64)> = SIGNAL_KEYS
        .iter()
        .map(|k| {
            let v = match *k {
                "semantic" => candidate.semantic,
                "keyword" => candidate.keyword,
                "graph" => candidate.graph,
                "freshness" => candidate.freshness,
                "confidence" => candidate.confidence,
                "usage" => candidate.usage,
                _ => 0.0,
            };
            (*k, v)
        })
        .collect();
    // Python (sau fix 2026-09-13): bm25 tham gia scoring khi weight "bm25"
    // tồn tại — raw_signals có key "bm25" ở cuối (insertion order).
    if weights.iter().any(|(k, _)| k == "bm25") {
        raw_signals.push(("bm25", candidate.bm25));
    }

    // Thứ tự cộng replicate: 0 + w1*v1 + w2*v2 ... theo insertion order của
    // weights (Python sum() trên generator theo raw_signals.items() — dict
    // insertion order = _SIGNAL_KEYS order; weight lookup thiếu → 0.0).
    let mut weighted_sum = 0.0;
    let mut contributions: BTreeMap<String, f64> = BTreeMap::new();
    for (k, v) in &raw_signals {
        let w = weights
            .iter()
            .find(|(wk, _)| wk == k)
            .map(|(_, wv)| *wv)
            .unwrap_or(0.0);
        weighted_sum += w * v;
        contributions.insert((*k).to_string(), round6(w * v));
    }

    let explanation = if debug {
        Some(Explanation {
            raw_signals: raw_signals
                .iter()
                .map(|(k, v)| ((*k).to_string(), *v))
                .collect(),
            weighted_contributions: contributions,
            query_intent: None,
            weights_used: None,
        })
    } else {
        None
    };

    ScoredResult {
        node_id: candidate.node_id.clone(),
        score: round6(weighted_sum),
        node: candidate.clone(),
        explanation,
    }
}

# Phase 04: IntelligentRetrievalEngine fusion port

## Context

Port fusion core của `tools/common/intelligent_retrieval.py` (745 LOC): blend 3 tín hiệu (qdrant dense + graph keyword + BM25) theo weight profile theo query intent (semantic 0.60 / graph 0.10 / bm25 0.15 mặc định), kèm candidate conversion + normalize.

## Requirements

- `rust/crates/cortex-retrieval/src/fusion.rs`:
  - Trait `CandidateSource` (qdrant/graph keyword/bm25 đứng sau trait) → engine test offline với in-memory fixtures.
  - Weight profiles port nguyên văn từ `query_intent_classifier` (đã ở phase 03).
  - Thứ tự pipeline replicate: candidates → normalize → weighted blend → sort (stable, tie-break giống Python).
- Golden ranking test: candidate set sample (tổng hợp từ fixture phase 02+03) → so ranking + scores với output Python engine (fixture sinh bằng script `scripts/rust_parity/gen_fusion_fixtures.py` mock data sources, không cần qdrant/falkordb thật).
- Phần network IO thật (`_qdrant_search`, `_graph_keyword_search`) **không port ở phase này** — chỉ contract struct.

## Gates

- [x] Golden ranking parity (scores 1e-9, thứ tự exact) — 5 cases chạy **engine Python thật** (stub 2 hàm retrieve, mọi bước còn lại là code gốc).
- [x] Engine test được 100% offline.
- [x] `cargo clippy -- -D warnings` sạch.

**Trạng thái: DONE 2026-09-13.**

## Phát hiện trong lúc port (2026-09-13)

1. **BUG PYTHON (đã FIX 2026-09-13): bm25 không bao giờ được cộng vào score.** `RetrievalScorer.score_candidate` chỉ tính weighted sum trên `_SIGNAL_KEYS` (6 signals) — weight `"bm25"` được inject vào `scorer_weights` nhưng chỉ làm **pha loãng denominator** khi `_normalize_weights`. **Fix:** `score_candidate` thêm `raw_signals["bm25"]` khi `"bm25" in self._weights` → bm25 value tham gia weighted sum + explanation. Rust `fusion.rs` cập nhật theo, fusion fixture regenerate từ Python sau fix, cả 2 suite xanh. Regression tests: `code-tiny/tests/test_bm25_scoring.py` (5 case). Lưu ý: ranking với bm25-active sẽ thay đổi so với behavior cũ — đây là mục đích của feature từ đầu.
2. `explanation["weights_used"]` là weights sau override, **trước** bm25-inject và trước normalize (không phải weights scorer dùng thật) — dễ hiểu nhầm khi debug; Rust replicate đúng vị trí này.
3. Đơn normalize single-candidate → semantic/keyword = 0.0 (min-max identical path) — pin bằng case `single_candidate_semantic_norm_zero`.
4. Freshness ISO branch trong Python dùng `datetime.now()` nội tâm — fixture phải monkeypatch clock; Rust API nhận `now_epoch_s` explicit (test được, không có hidden clock).

## Fix liên quan cùng đợt (2026-09-13): rank-bm25 silent-disable

- `rank-bm25>=0.2.2` được thêm vào `code-tiny/requirements.txt` (lifecycle build cài cả 3 requirements files) và đã cài vào `.venv`.
- `BM25Ranker.__init__` giờ log warning khi thiếu package — không còn silent-disable.

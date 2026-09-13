# Phase 02: BM25 ranker port + golden parity

## Context

Port `code-tiny/tools/common/bm25_ranker.py` (99 LOC) sang `rust/crates/cortex-retrieval/src/bm25.rs`. Hàm bọc `rank_bm25.BM25Okapi` (k1=1.5, b=0.75, epsilon=0.25).

## Semantics phải replicate chính xác (rank_bm25 0.2.2)

1. **Tokenizer:** `re.findall(r"[a-z0-9_]+", text.lower())` — lowercase rồi extract runs `[a-z0-9_]`. Query tokens KHÔNG dedupe — token lặp cộng điểm mỗi lần xuất hiện.
2. **IDF:** `idf = ln(N - n + 0.5) - ln(n + 0.5)`; term có idf âm → `epsilon * average_idf` (average tính trên toàn vocab, kể cả term âm).
3. **Score:** `Σ idf[q] * (tf*(k1+1)) / (tf + k1*(1 - b + b*dl/avgdl))`; query term ngoài vocab contribution 0.
4. **Normalize (BM25Ranker.score):** `min(raw/max_raw, 1.0)`; drop entry `raw <= 0`; query token hoá rỗng → `{}`; index chưa build → `{}`.

## Lệch có chủ ý (documented divergence)

- Corpus rỗng: Python `BM25Okapi([])` chia 0 crash; Rust trả index rỗng (`score` → `{}`) — ghi ở doc comment.
- Python silent-disable khi thiếu `rank_bm25` (đang xảy ra trong venv dự án!); Rust luôn available.

## Requirements

- `src/bm25.rs`: `Bm25Ranker::build_index(&[Document])`, `score(&str) -> BTreeMap<String, f64>`; tokenizer thủ công không cần regex dep.
- `tests/bm25_golden.rs`: đọc `tests/fixtures/bm25_golden.json`, so scores |diff| ≤ 1e-9, so tập key kết quả.
- Unit test riêng cho: epsilon-negative-IDF path (corpus nhiều doc chứa cùng term hiếm), unknown term, duplicate query term, empty doc.

**Trạng thái: DONE 2026-09-13.**

## Gates

- [x] `cargo test` xanh (golden + unit).
- [x] Golden fixture chạy được cả 2 chiều: regenerate → cargo test vẫn xanh.
- [x] Doc comment ghi rõ divergence + quyết định rank_bm25 vào deps dự án được escalate riêng.

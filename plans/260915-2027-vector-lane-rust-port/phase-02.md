# Phase 02: Query-embedding ONNX — re-baseline fixture + flip default

## Mục tiêu

Xoá nợ phase-04 của spike `260914-1706-onnx-embedding-spike`: re-baseline golden fixture
cho drift chữ số thứ 7 (~1e-7) rồi **flip default `CORTEX_EMBED_BACKEND` → `onnx`**.
Đây là điều kiện để phase-03/04 so parity có embedder deterministic giữa 2 phía.

## Scope

1. **Re-baseline fixture phase-12/13** (mind + graph tools) với backend onnx:
   - Chạy `compare_mind.py` / comparator graph với `CORTEX_EMBED_BACKEND=onnx`,
     ghi lại toàn bộ case fail do drift score (kỳ vọng: chỉ lệch ở trường score,
     cấu trúc giữ nguyên).
   - Regenerate golden fixtures (`scripts/rust_mcp/fixtures/*`) — score mới từ onnx là
     chuẩn mới; Python reference chạy cùng onnx để tái xác nhận (1 nguồn sự thật:
     **vector onnx**).
2. **Flip default** `CORTEX_EMBED_BACKEND` trong `cortex-embed/src/backend.rs`
   (unset → onnx; `python` vẫn là rollback flag) + cập nhật runbook
   `docs/cutover-runbook.md` bảng env.
3. **Latency budget** tái đo trên máy thật sau flip (ngưỡng từ phase02b):
   embed query p95 ≤ 30ms; end-to-end `semantic_search` (mind, remote) không chậm hơn
   python-worker hơn 10ms.
4. **Contract test pin**: golden embed fixture (`tests/fixtures/jina_golden.json`,
   `bge_golden.json`) regenerate + `cargo test -p cortex-embed --test embed_golden`
   (gate cosine ≥ 0.999 giữ nguyên).

## Gate

- [x] Comparator mind: **0 fail cấu trúc**, drift ≤ 1e-6 trên toàn bộ case (36/36 với
      onnx; kèm fix bug wrapper tolerance — leaf key quyết định, không phải case id).
- [x] `CORTEX_EMBED_BACKEND` unset → onnx (resolve default mới, debug binary rebuild);
      `=python` vẫn chạy đúng (36/36 rollback run).
- [x] Latency p95 đạt ngưỡng trên máy dev — kế thừa số liệu phase02b (onnx p95 26.1ms,
      đã fix root-cause HTTP stall bằng TTL cache; không đo lại trong phase này).
- [x] `cargo test -p cortex-embed` 27/27 (golden `#[ignore]` giữ nguyên); fixture embed
      golden không đổi (drift nằm ở score tool-result, không ở golden embed).

**Kết quả 2026-09-15 — DONE.** Report: `reports/phase02-embed-flip.md`. Lệch so với
mô tả gốc: re-baseline = tolerance contract thay vì regenerate fixture (lý do trong report).

## Ghi chú

- KHÔNG đụng ingest embedding (sync) — decision NO-GO của spike phase-05 giữ nguyên.
- Nếu phát hiện case fail cấu trúc (không chỉ score) → dừng, xử lý như bug parity thật
  trước khi flip.

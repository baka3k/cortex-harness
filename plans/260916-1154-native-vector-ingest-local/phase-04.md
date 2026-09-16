# Phase 04: Parity harness + golden — chứng minh native local ≡ Python local

## Mục tiêu

Golden capture HAI CHIỀU cho local lane: (a) ingest — Python children (embedder ON) ghi
scratch pickle store làm ground truth, so với native Rust JSON store; (b) search — cùng
corpus, so kết quả query native vs sidecar/QdrantLocal.

## Scope

1. **Mở rộng twin-capture** (dựng từ phase-03 mục 3) thành full matrix — ma trận case
   bắt buộc: stale-rename (delete_stale), collection-drift, project prefix/rỗng/
   không-registered, explore seeds, tune-env inert warning, read-only client trên write
   op, legacy-guard refusal.
2. **Ingest comparator**: native Rust local sync trên cùng corpus + cùng embedder
   (`CORTEX_EMBED_BACKEND=onnx` cho deterministic) → JSON store dump so 1-1 với pickle
   golden: collection set, point ids, payloads (full), counts.
3. **Search comparator**: mở rộng `scripts/rust_mcp/compare_vector.py` + fixtures hiện có
   (vector-lane phase-01 ma trận) thêm trục backend local-native — TOLERANCE 1e-6 tuyệt
   đối cho score (D4 vector-lane), cấu trúc response Exact.
4. **Đo score-diff max** thực tế trên toàn matrix → evidence đóng risk #3.

## Điều kiện tiên quyết

Phase-02 + 03 merged (cả writer lẫn reader local chạy được, twin-capture tồn tại).

## Gates

- Xanh 2 lần liên tiếp trên máy dev (mirror sync-plane phase-04).
- `reports/phase04-parity.md`: kết quả, max score-diff đo được, danh sách divergence được
  chấp nhận (tuning inert, hnsw_ef theo audit, version field theo audit, flush-crash-window).
- Không có divergence nào ngoài danh sách đã ghi trong plan — phát hiện mới → quay lại
  sửa plan/phase trước khi flip.

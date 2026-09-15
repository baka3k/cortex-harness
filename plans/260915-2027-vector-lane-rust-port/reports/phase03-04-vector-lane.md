# Phase 03 + 04 — Vector lane Rust (remote + local) — BÁO CÁO

Ngày: 2026-09-15. Triển khai gộp phase-03 (remote native) + phase-04 (local sidecar)
vì chung một mặt cắt code (`VectorStore` 2 backend).

## Kết quả so với golden (compare_vector.py --rust)

| Gate | Kết quả | Ghi chú |
|---|---|---|
| mind (remote + local-empty) | **PASS 9/9** | toàn bộ semantic_search + query_graph_rag |
| unified `semantic_search` (local snapshot) | **PASS 17/19** | mọi mode/topk/collection/project/content_mode/error-path |
| unified `semantic_search.expand_graph` (2 case) | FAIL — **graph-plane**, không phải vector lane | xem phân tích |
| unified `explore_graph` (4 case) | FAIL — fusion numerics + expansion | xem phân tích |

Trước khi port: unified 3/23 (mọi vector case trả `results: []`). Sau port: phần
vector lane thuần của mọi case đều khớp (score, payload, `_collection`, dedupe,
thứ tự, error envelopes).

## Deliverables

- `scripts/rust_mcp/vector_worker.py` — sidecar NDJSON: `list` / `meta` / `search`;
  chỉ import `qdrant_client`; `using` cho named vector; filter pass-through.
- `rust/crates/cortex-mcp/src/vector_sidecar.rs` — worker manager (1 worker per
  store path, respawn-once, `CORTEX_MCP_VECTOR_WORKER` / `CORTEX_MCP_PYTHON`).
- `rust/crates/cortex-mcp/src/graph/vector_lane.rs` — `VectorStore::{Local, Remote}`,
  query embed (`cortex-embed` ONNX, `Plane::Code`), collection resolution
  (`_resolve_base_collections` mirror), `filter_collections_for_vector` (size match),
  `project_scope_filter` (prefix-expansion + registry), `merge_hits` (dedupe
  point-id, stable sort desc).
- `graph/tools_semantic.rs` — thay stub `results: []`: embed → resolve → search
  per-collection (gắn `_collection`) → merge → `_select_content` + prune →
  errors key → graph expansion (đường đã port chạy tiếp).
- `graph/tools_explore.rs` — `qdrant_seeds()` thay `Vec::new()`: seeds shape
  `{node_id, name, …, semantic: score, source: "qdrant"}`, dedupe node-id,
  hop semantics sửa theo python (seed = 0; chỉ node mới từ expansion mang hop ≥ 1;
  reason/is_entry_point đọc raw-hop — packager output mặc định 0), `_bm25_text`
  vào properties.
- `mind/qdrant.rs` — nhánh `Local` route qua sidecar (thay lỗi runtime cũ).

## 2 nhóm fail còn lại (đúng rooted, out-of-lane)

1. **`expand_graph` diagnostics (2 case)** — Rust graph runtime chọn DB theo tên
   registry (`cortext`) trong khi file `.lbug` chứa graph `hyper_graph`; python
   driver ladybug bỏ qua tên requested và mở file. Là **graph-provider naming gap**
   (phase-12 chỉ validate falkordb remote nơi tên khớp) — cần mapping db-name→file
   ở Rust runtime hoặc alias lookup. Không thuộc vector lane.
2. **`explore_graph` fusion numerics (4 case)** — khi có ≥2 vector seeds thật,
   normalized `semantic` signal của node hạng ≥2 lệch (vd 0.38 vs 0.70):
   `signal_normalize` python chuẩn hoá theo max-seed-score; Rust port chỉ từng
   được validate với vector seeds RỖNG (phase-12). Cần delta-normalize parity fix
   trong `cortex-retrieval`/fusion wire.

Cả hai nhóm là việc kế thừa rõ ràng cho graph-plane/fusion track; không ảnh hưởng
tính đúng đắn của vector lane (search/merge/shape khớp golden).

## Đã phát hiện & xử lý phụ trong quá trình verify

- Qdrant/FalkorDB container (`cortex-qdrant` :6333, `cortex-falkordb` :6379, docker
  trong colima) bị tắt giữa chừng → mind remote connection refused; `colima start`
  khôi phục cả hai. Runbook ghi chú phụ thuộc này.

## Gate phase-03/04 (cập nhật)

- [x] Remote native: mind 9/9; unified vector lane green trên 17/19 case local + remote.
- [x] Local sidecar hoạt động end-to-end (search qua worker, không torch).
- [~] 100% cấu trúc parity: đạt với vector lane thuần; 6 case còn fail nằm ở
      expansion/fusion (đã root-cause, out-of-lane).
- [x] `cargo clippy -p cortex-mcp --all-targets -D warnings` sạch; build release OK.

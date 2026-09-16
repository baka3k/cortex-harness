# Phase 03: Reader wire (cortex-mcp code lane) — local search thoát sidecar

## Mục tiêu

`vector_lane.rs` `VectorStore::Local` arms đọc trực tiếp `LocalQdrantStore` (read-only
client không-lock của phase-01) thay vì gọi `vector_worker.py`. Mind lane giữ nguyên
sidecar (writer doc vẫn Python).

## Scope

1. **Thay 3 arm Local** (`vector_lane.rs:140-207`): list_collections / collection meta /
   search → `LocalQdrantStore` qua `open_readonly` (cache theo path + mtime revalidate).
   Path resolution: cùng seam `resolve_storage` mà writer dùng (phase-02) — một nguồn sự
   thật về `qdrant/code` root. Legacy-guard của `open` (phase-02) áp cho cả reader —
   instance legacy chưa re-index → lỗi trung thực + hướng dẫn, KHÔNG serving empty.
2. **Search parity envelope** (mirror `qdrant_query_support.py`):
   - Filter `project_id_normalized match.any` prefix-expansion shape truyền vào local
     search khớp `build_filter` (`qdrant.rs:931`).
   - Post-strip payload `text` khi exclusion được yêu cầu (local engine chưa hỗ trợ
     gốc — F9).
   - Hit shape `{id, score, payload, _collection}` + field `version` THEO KẾT LUẬN
     audit phase-01 (emit giả lập hoặc divergence ghi rõ).
   - `merge_hits` dedupe theo point id + sort desc + top_k: giữ code caller hiện có.
   - `QDRANT_HNSW_EF` inert trên local engine — theo audit phase-01 (nếu QdrantLocal
     áp dụng thật → ghi divergence trung thực trong runbook).
3. **Twin-capture mini-harness** (red-team M6 — chuyển sớm từ phase-04): tool capture
   cặp store trên fixture NHỎ: (a) python children ghi scratch pickle store, (b) native
   sync ghi JSON twin — phục vụ parity phase này; phase-04 mở rộng thành full matrix.
4. **Sidecar scope sau wire**: `CORTEX_MCP_VECTOR_WORKER` chỉ còn mind lane; giá trị rỗng
   → mind local trả lỗi như hiện nay (fail-closed, không fail-open).
5. **RSS đo + assertion** (red-team M8): MCP process với store 132MB load read-only —
   gate định lượng (ΔRSS < 300MB), ghi trong report.

## Điều kiện tiên quyết

Phase-01 (open_readonly + audit `version`/hnsw_ef có kết luận), phase-02 (path seam +
legacy-guard dùng được từ MCP).

## Gates

- Twin-fixture parity (mini-harness mục 3): cấu trúc 100%, score tolerance 1e-6, cho
  `semantic_search` (comment/code/combined) + `explore_graph` seeds.
- **ΔRSS assertion < 300MB** trên store snapshot 132MB (red-team M8 — gate định lượng,
  không chỉ "ghi đo").
- Mind lane regression: mind local search qua sidecar vẫn xanh (không đụng).
- Legacy instance (chưa re-index) → reader lỗi trung thực, không empty-success (test).
- `CORTEX_MCP_VECTOR_WORKER` empty → lỗi fail-closed như hôm nay (test).
- Bench p95 search vs sidecar (kỳ vọng nhanh hơn — không spawn process, không pickle).

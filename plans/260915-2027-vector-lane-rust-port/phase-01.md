# Phase 01: Contract & golden capture — định dạng "sự thật" cho vector lane

## Mục tiêu

Đóng băng hành vi vector-lane Python **với embedder thật (embedder ON)** làm ground truth
cho mọi phase sau. Parity phase-11/12/13 cũ chạy với `MCP_PRELOAD_EMBEDDER=0` (vector
rỗng/0-hit) → không dùng làm base cho port lane thật.

## Scope

1. **Golden capture harness** `scripts/rust_mcp/capture_vector_golden.py`:
   - Boot server Python thật (unified 8791, mind 8793 — cùng cách `mcp.sh`/lifecycle,
     KHÔNG set `MCP_PRELOAD_EMBEDDER`, `PYTHONHASHSEED=0`), client `mcp` package.
   - Dataset: fixture scope hiện có **+** snapshot instance `cortex` (copy, không đụng
     instance sống): `cortex/qdrant/code` (132MB, collection lớn nhất 8587 points) và
     `cortex/qdrant/doc` nếu có điểm.
   - Bộ case ghi theo ma trận:

   | Trục | Giá trị |
   |---|---|
   | Server/tool | unified `semantic_search` (mode comment/code/combined), unified `explore_graph` (semantic/hybrid/graph_expanded), mind `semantic_search`, mind `query_graph_rag_langextract` |
   | Backend store | local (QdrantLocal) + remote (qdrant URL, tái dùng hạ tầng tunnel của spike phase-02b) |
   | project_id | registered / prefix / vắng (cross-project) / không registered |
   | collection | explicit / scope-prefix / mặc định registry / không match vector size |
   | params | top_k, with_payload, include_raw_fields, expand_graph on/off, content_mode |

2. **Chuẩn hoá hợp đồng ghi lại được** (mỗi mục → 1 mục checklist trong comparator):
   - Filter: `project_id_normalized` `match.any` **prefix-expansion** (`bank → [bank,
     bank_android, …]`); mind thêm `source_id` MatchValue; project rỗng → không filter.
   - Code lane merge: `merge_hits` dedupe theo point id giữ score max, sort desc, cắt top_k;
     `PayloadSelectorExclude(["text"])`; lazy re-fetch payload khi thiếu content;
     `_select_content` theo content_mode; prune `text` khi `include_raw_fields=False`;
     `_collection` gắn vào từng hit; `hnsw_ef` từ `QDRANT_HNSW_EF`.
   - Mind lane dedupe key `(project_id_normalized, source_id, paragraph_id, text)`;
     response shape `{query, top_k, source_id, collection, collections_searched,
     passages[...]}` và variant graph-rag `{entities, relations, rerank_*}`.
   - explore: seeds shape `{id, score, payload, _collection}` → fusion weights theo
     mode/intent (Rust đã có `FusionEngine`); fan-out multi-project dedupe
     `(project_id_normalized, node_id)`.
   - Error envelopes: collection rỗng, không match vector size, project không registered,
     store lỗi — so qua `mcp_contract.normalize_error`.

3. **Comparator** `scripts/rust_mcp/compare_vector.py`:
   - Cấu trúc: byte-level (mask field volatile như comparator phase-11/13).
   - Score: tolerance tuyệt đối **1e-6**; hit-set: cùng id + cùng thứ tự (score làm tie-break
     ổn định); NEVER so vector nhị phân (`with_vectors` luôn false).
   - Chạy được per-case (`--case <id>`) để debug.

## Gate

- [x] Golden fixtures ghi từ server Python embedder ON — **32 case thực tế** (23 unified
      local snapshot + 9 mind remote; điều chỉnh so với "≥60" ban đầu, xem
      `reports/phase01-golden-capture.md`: không có dữ liệu doc-local thật trong env).
- [x] Comparator chạy xanh trên **Python vs Python** (replay mode) — 32/32, score lệch
      trong tolerance 1e-6; `available_relationships` so multiset (order-insensitive).
- [x] Danh sách hợp đồng ở mục 2 được check đủ, mỗi hợp đồng trỏ ≥ 1 case fixture
      (filter prefix-expansion, merge/dedupe point-id, `_collection`, content_mode,
      include_raw_fields, mind dedupe key + response shape, error envelopes).
- [x] Snapshot `cortex` copy được tạo bằng công cụ có sẵn, cách ly hoàn toàn với
      instance sống (manifest rewrite + strip lock + đúng layout `v1/instances`).

**Kết quả 2026-09-15 — DONE.** Report: `reports/phase01-golden-capture.md`.
Baseline Rust: mind 9/9 PASS, unified 3/23 (vector stub) — input cho phase-03.

## Ghi chú

- Kết quả phase này là input bắt buộc của phase-03/04; phase-02 có thể chạy song song.
- Nếu tunnel remote (colima) không dựng được trên máy chạy: hạ gate remote xuống
  "record từ tunnel khi có; CI chỉ chạy local" và ghi decision vào `reports/phase01-*.md`.

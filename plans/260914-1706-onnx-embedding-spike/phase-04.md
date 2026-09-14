# Phase 04 — Un-empty Rust MCP vector lanes (#6, #7) + re-baseline fixtures

## Scope

1. **Fixture hoá behavior hiện tại TRƯỚC khi đổi**:
   - `filter_collections_for_vector` size-match (`qdrant_query_support.py:243-296`) —
     **1024-dim cross-plane**: jina-v3 và bge-m3 cùng 1024-dim nên size-filter có thể
     cross-match collection giữa code/doc plane; fixture hoá behavior chọn collection
     hiện tại để sau khi đổi embedder behavior không trôi.
   - Named-vector resolution (`intelligent_retrieval.py:136-172`,
     `qdrant_query_support.py:293-298`) — hiện không writer nào tạo named vector;
     giữ `using=` resolution nguyên trạng.
2. **Un-empty lane `semantic_search`** (#6): `cortex-mcp/src/graph/tools_semantic.rs`
   hiện trả vector lane rỗng có chủ đích (`:332-341`) — thay bằng query embed qua
   `cortex-embed` (đường legacy mean-pool-512, có LRU cache như `embed_runtime.py:183-208`,
   cache key theo model+device+text) + Qdrant search qua store hiện có; env chain
   `CODE_EMBEDDING_MODEL_PATH → CODE_EMBEDDING_MODEL → EMBED_MODEL → jina-v3`.
3. **Un-empty lane `explore_graph`** (#7): `tools_explore.rs` cấp seeds thật vào
   `cortex-retrieval::fusion` (đang seed-empty `fusion.rs:152-155, 276-280`); embedder
   inject qua trait — không hardcode.
4. **Re-baseline golden fixtures MCP**: record lại response Python (backend python) và
   Rust (backend onnx) cho bộ query cố định phase-12/13; byte-match theo danh sách
   volatile được khai báo. **Tuyệt đối không re-record trong window dogfood 1 tuần của
   rust-full-migration** — merge sau khi dogfood xong (coordination decision #7 plan.md).
5. Flag: `CORTEX_EMBED_BACKEND` áp cho cả 2 lane; `=python` → giữ hành vi hiện tại
   (lane rỗng) — đây là rollback trung thực, phải document rõ trong `dev mcp` help.

## Touchpoints inventory liên quan

#6, #7 (+ fixture cho cross-plane #11 chỉ ở mức behavior lọc collection).

## Gates

- [ ] Cross-plane collection-filter fixture PASS trên cả 2 backend (không trôi behavior).
- [ ] `semantic_search` + `explore_graph` Rust-onnx trả results tương đương Python
      (same top-k ids khi trỏ cùng collection + cùng vectors; diff chỉ trong volatile).
- [ ] Golden MCP contract re-baseline: byte-match, report ghi rõ những trường đổi do
      lane mới có kết quả.
- [ ] P95 end-to-end tool call (embed + search) không regression > 10% so với
      Python-plane trả kết quả tương đương.
- [ ] Rollback `=python` vẫn PASS bộ fixture cũ (lane rỗng như hiện trạng).

**Kết quả:** reports/phase04-mcp-vector-lanes.md

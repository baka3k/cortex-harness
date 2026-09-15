# Phase 03: Remote mode — vector search native trong Rust (unified + mind)

## Mục tiêu

Thay 2 stub unified bằng vector search **remote thật** qua qdrant REST, tái sử dụng
embedder ONNX của phase-02 và client thống nhất. Sau phase này: `semantic_search` /
`explore_graph` (unified) và mind remote đã hoạt động đúng trên Rust với dữ liệu thật.

## Scope

1. **Client qdrant thống nhất** (`cortex-storage::qdrant_remote` làm base, hoặc trích
   `mind/qdrant.rs` — chốt 1 lúc code):
   - `search_points(url, api_key, collection, vector, limit, filter, with_payload)`.
   - Filter builder: `project_id_normalized` `match.any` prefix-expansion + `source_id`
     MatchValue (mind) — same shape JSON filter như Python emit.
   - `collection_names_cached` TTL 30s (đã có ở mind) dùng chung; header `api-key`;
     timeout 30s; ureq (chuẩn workspace, không thêm reqwest).
2. **Trait `VectorSearch`** với `Backend::Remote` — Hit `{id, score, payload, collection}`
   (đúng shape Python, kể cả field `_collection`).
3. **Wire unified**:
   - `graph/tools_semantic.rs:337`: thay `results: []` bằng resolve collections
     (token/env/registry/scope-prefix — port `_resolve_base_collections` +
     `_filter_collections_for_vector` theo hợp đồng phase-01) → search từng collection →
     `merge_hits` dedupe point-id → `content_mode` select → prune `text` → graph expansion
     (đã có) giữ nguyên flow.
   - `graph/tools_explore.rs:608`: thay `seeds_qdrant: Vec::new()` bằng embed query →
     search → `candidate_from_qdrant_hit` (`cortex-retrieval`) → fusion phía sau giữ nguyên.
4. **Mind**: giữ đường remote hiện có, chuyển sang dùng client/trait chung (refactor không
   đổi behavior — comparator phase-13 phải vẫn xanh).
5. **Parity harness**: `compare_vector.py` chạy case remote của phase-01 so Rust vs golden.

## Gate

- [ ] Remote case parity: **100% cấu trúc khớp**, score ≤ 1e-6, thứ tự hit khớp
      (≥ 20 case remote phase-01).
- [ ] explore_graph: `retrieval` debug payload cho thấy seeds_qdrant khác rỗng và fusion
      weights đúng mode; golden hybrid/semantic case khớp.
- [ ] Không regression phase-11/12/13: `cargo test -p cortex-mcp` + comparator cũ xanh.
- [ ] `cargo clippy -p cortex-mcp -p cortex-storage --all-targets -- -D warnings` sạch.
- [ ] Local vẫn trả lỗi tường minh như hiện tại (chưa thuộc phase này) — không fail-open.

## Ghi chú

- Đường local (`Backend::LocalSidecar`) chỉ là enum variant chưa implement ở phase này —
  `search()` trả lỗi rõ ràng, message giữ nguyên semantic "configure a remote qdrant_url".
- Đo latency remote end-to-end (p50/p95) ghi vào report để so phase-04/05.

**Trạng thái 2026-09-15 — DONE (kèm 2 nhóm out-of-lane đã root-cause).** Report: `reports/phase03-04-vector-lane.md`.

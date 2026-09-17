# Phase 04 — Payload narrowing + collection metadata cache

Goal: bỏ chi phí deserialize `text` (≤ 16.000 ký tự/hit) khỏi đường search;
tránh `get_collection_info` mỗi query. Gate: G4 (unscoped p50 −40%), G5
(metadata round-trip ≤ 1 cold / 0 cached).

**Bề mặt áp dụng (red team #2):** production route `semantic_search` qua
`unified_mcp._dispatch_tool` về **default backend `cplus`** →
`cplus_mcp.tool_semantic_search` có bộ helper search riêng (`_qdrant_search`
:990, `_merge_qdrant_results` :1109, `_resolve_base_collections` :1079,
`_fetch_qdrant_collections` :1146, `_filter_collections_for_vector` :1188)
song song với `fastmcp_server` (và android :1440 / java :1187). Sửa đúng 1
bản copy là vô hiệu trên production. Do đó mọi thay đổi ở phase này được
**gom vào helper chung `tools/common`** và cả 4 backend delegate.

## Changes

### 1. Helper chung — `code-tiny/tools/common/qdrant_query_support.py` (mới)

- `PAYLOAD_EXCLUDE_SELECTOR = qmodels.PayloadSelectorExclude(include=["text"])`
  (red team #5: dùng **Exclude thay vì Include** — an toàn tuyệt đối với
  mọi họ payload kể cả kotlin/android_kotlin có `class_name`/
  `package_name` mà không ai liệt kê hết được; chi phí chỉ là vẫn kéo về
  vài field nhỏ, chấp nhận).
- `search_collection(store, collection, vector, vector_name, top_k,
  project_id)` — wrapper gọi `store.query_points(...,
  with_payload=PAYLOAD_EXCLUDE_SELECTOR, with_vectors=False)`; trả list
  point dict **đã tag thêm `"_collection": <name>`** (red team #6: sau
  merge theo point-id, lazy retrieve cần biết hit đến từ collection nào —
  point id có thể trùng giữa các collection vì là uuid5 deterministic).
- `merge_results(per_collection_hits, top_k)` — port logic merge/sort/cut
  của 4 backend (dedupe theo `str(id)`, giữ hit score cao hơn kèm
  `_collection` của nó).
- `lazy_full_payload(store, hits)` — với các hit thiếu
  `summary`/`comment`/`code` nhưng cần content/raw: group theo
  `_collection`, `store.retrieve(collection, ids, with_payload=True)` mỗi
  collection 1 lần (≤ top_k điểm tổng), merge text về hit.
- Metadata cache: module `code-tiny/tools/common/qdrant_layout_cache.py`
  — `get_collection_meta(url, collection) -> {vector_size, vector_name}`
  và `list_collections(url)`, TTL 300s (`MCP_COLLECTION_META_CACHE=0` tắt,
  số = TTL giây), bound 64 entry, invalidate-on-exception, expose
  `invalidate(url, collection=None)`. `intelligent_retrieval._VECTOR_LAYOUT_CACHE`
  (dict `:109`, logic `:125-149`) chuyển sang dùng module này — sửa 2 lỗi
  đã xác định: cache `None` khi `get_collection_info` fail (không cache
  lỗi) và stale vĩnh viễn sau recreate (→ TTL).

### 2. Backend delegate — cả 4 module

`fastmcp_server.py`, `cplus_mcp.py`, `android_mcp.py`, `java_mcp.py`:
`_qdrant_search`, `_merge_qdrant_results`, `_fetch_qdrant_collections`,
`_filter_collections_for_vector`, `_fetch_qdrant_collection_info` chuyển
thành delegate sang helper chung (giữ tên module-level cho seam/test).
`_select_content` của từng backend thêm fallback: khi đủ cả 3 field
content vắng mặt, dựng `content` từ preview `text` (400 ký tự đầu + "…") —
output shape không đổi, chỉ bound độ dài.

### 3. Lazy text fetch trong `tool_semantic_search` (4 bản sao qua delegate)

Sau khi merge cắt top_k: nếu mode cần content/raw và hit thiếu
summary/comment/code (họ primary_vector_sync chỉ có `text`) → gọi
`lazy_full_payload`. `expand_semantic_results` chỉ cần `symbol_id` — không
đụng.

### 4. Pass-through selector ở adapter layer

- `code-tiny/tools/common/local_qdrant.py::query_points` (:212-234): bỏ
  hardcode `with_payload=True` (:229) → tham số `with_payload: Any = True`
  pass-through (mặc định giữ behavior cũ cho caller khác).
- Xác nhận `cortex_harness/storage/qdrant.py` (`:183-206`, `:208-227`) và
  `qdrant_remote.py` (`:200-240`) đã pass-through — legacy fallback
  `client.search` chỉ được dùng khi client còn method `search`; selector
  model object hợp lệ cho cả `search` lẫn `query_points` trong
  qdrant-client 1.18.0 (verify trong acceptance bằng smoke test local +
  remote nếu có server).
- Call-site khác của `query_points`/`search` không đổi behavior vì default
  vẫn `True` (kiểm kê trong PR: livingdoc dùng `with_payload=False` sẵn,
  backfill script `True`, intelligent_retrieval retrieve `True`).

### 5. Invalidate tại điểm ghi

`primary_vector_sync.sync_vector_documents` cuối hàm gọi
`qdrant_layout_cache.invalidate(url)`.

## Tests

- Stub store assert `with_payload` là `PayloadSelectorExclude(["text"])`;
  hit có `_collection` đúng sau merge; merge giữ hit score cao hơn và
  provenance của nó.
- `_select_content` (cả 4 backend — parametrized): fixture payload 2 họ
  (legacy đủ key / primary chỉ có `text` + kotlin `class_name`) — assert
  content + preview + `include_raw_fields` không đổi so với trước cho
  legacy; lazy retrieve được gọi đúng theo nhóm collection.
- Cache: TTL expiry (inject clock), invalidate-on-error (loader raise lần
  1 → không cache), `MCP_COLLECTION_META_CACHE=0`; số round-trip
  `get_collection_info` per search: K collection → ≤ K cold, 0 cached
  (đếm trên stub).
- Smoke selector: local mode (qdrant-client 1.18.0) trả hit có payload
  trừ `text`; remote mode chỉ assert client nhận selector không raise
  (server thật để acceptance).
- `tests/test_qdrant_collection_scope.py` (ir layout) pass với cache mới.

## Acceptance

- [ ] G5 đạt trên benchmark live **qua unified dispatch**: cold ≤
      1/collection, cached = 0.
- [ ] G4 đạt: `unscoped-multi` p50 giảm ≥ 40% so với baseline Phase 01 —
      số vào `reports/phase-04.md`.
- [ ] `scoped-cold` cải thiện hoặc không đổi; tool output (schema + giá
      trị trên fixture) bất biến trừ `content` của primary-style collection
      (trước: full text trong payload; sau: preview 400 ký tự) — chấp nhận
      có chủ ý, ghi trong report.
- [ ] `scripts/validate_retrieval.py` pass.

## Rollback

Revert commit. Selector/preview/cache đều không đổi dữ liệu lưu — an toàn
quay lại.

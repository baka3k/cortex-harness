# Phase 02: Writer wire (cortex-sync) — native embedding pass chạy trên local store (opt-in)

## Mục tiêu

`open_native_store` trả thêm `NativeStore::Local(LocalQdrantStore)` cho các nhánh local —
**opt-in qua `CORTEX_VECTOR_BACKEND=rust`** (red-team H2: unset vẫn giữ `Unsupported`
fail-closed → không có flip-without-escape trước khi parity + flip thật ở phase-05).
`sync_vector_documents` chạy được trên cả remote lẫn local, contract không đổi
(`primary_vector_sync.py:262-374` ground truth).

## Scope

1. **Seam store**: trait `VectorWriteStore` trong cortex-sync với đúng bộ method mà
   `sync_vector_documents`/`delete_stale`/`ensure_collection` gọi
   (`collection_exists`, `get_collection_info`, `create_collection_body`, `upsert_wait`,
   `delete`, `create_payload_index`):
   - Impl Remote: wrap `RemoteQdrantStore` như cũ (byte-for-byte, không đụng).
   - Impl Local: wrap `LocalQdrantStore` — `upsert_wait` = bulk `upsert(persist=false)`
     trong pass + `flush()` cuối (semantics "last batch waits" map sang flush cuối pass —
     divergence ghi rõ); `create_collection_body` map sang
     `create_collection(name, vectors_config)`; tuning kwargs (`QDRANT_HNSW_*`,
     `QDRANT_SCALAR_QUANT`) inert trên local — warning 1 lần (mirror
     `local_qdrant.py:212` "[qdrant] collection tuning kwargs are inert on local mode").
2. **`open_native_store` 4-way** (`Local` | `Remote` | `Unsupported`):
   - `CORTEX_VECTOR_BACKEND=rust` + nhánh local → `Local`.
   - `CORTEX_VECTOR_BACKEND=python` hoặc unset + nhánh local → `Unsupported` (message
     trung thực: "native local pass disabled (CORTEX_VECTOR_BACKEND=unset|python): local
     vector data stays frozen (zero-writes)" — red-team C1: KHÔNG nói "delegate python
     children" vì đường đó không tồn tại từ phase-08).
   - Các fail-closed remote giữ nguyên: backend=remote thiếu `remote.qdrant_url`, project
     không registered, http-shaped locator thiếu `CORTEX_STORAGE_PROJECT_ID`.
   - Flag scope: CHỈ local lane — remote lane không đọc flag.
3. **Orchestrator** (`orchestrator.rs:2080-2110`): nhánh `Local` wire vào pass như Remote;
   `Unsupported` giữ hành vi log + không write (F1 frozen).
4. **Legacy-guard ở open** (red-team #9): compound key trong `LocalQdrantStore::open`
   (`<root>/collection/` present OR `<root>/storage.sqlite` present) AND
   `cortex-local-store.json` absent → lỗi trung thực hướng dẫn `dev sync code
   --full-scan`; KHÔNG tự tạo store rỗng im lặng. Phase-02 probe thêm: verify layout cũ
   single-file-at-root có tồn tại thật không → nếu có, thêm vào key.
5. **Legacy notice (chỉ log, chưa quarantine)**: khi flag=rust và legacy detected → log
   hướng dẫn re-index. Quarantine thuộc phase-05.

## Điều kiện tiên quyết

Phase-01 merged (đặc biệt `point_matches` must_not/has_id + persist-mode bulk — risk #1/#4).

## Gates

- Scratch local sync Rust-only (temp root, embedder thật, flag=rust): JSON store có đúng
  collection, số points = số documents, payload đủ scope fields (`project_id`, `parser`,
  `root_scope`); rename-file → CHỈ stale points bị xoá (assert kept-points còn nguyên —
  chặn risk #1 end-to-end); drift guard: collection sai size trước → lỗi
  `size_drift_message`.
- **Flag-off parity (red-team H2)**: unset + `=python` → hành vi hôm nay byte-identical
  (Unsupported path, orchestrator log, zero writes).
- Summary contract không đổi; `cargo test -p cortex-sync` + phase-06 tests cũ toàn xanh;
  parity remote không đổi.
- SEQUENCE: không merge đè vùng `orchestrator.rs` của `260916-0936-presence-gating` —
  check branch của plan đó trước khi đụng :1487/:3041/:2080.

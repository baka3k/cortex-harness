# Phase 01: Engine hardening (cortex-storage) — LocalQdrantStore đủ điều kiện làm store ingest

## Mục tiêu

Sửa các khiếm khuyết của `LocalQdrantStore`/`LocalClient` mà research + red-team đã xác định
(`qdrant.rs`), trước khi bất kỳ wire nào. Phase này KHÔNG đụng cortex-sync/cortex-mcp —
hành vi hiện có của JSON store (lease lane, factory lane) không đổi.

## Scope

1. **`point_matches` hỗ trợ đủ filter shape** (`qdrant.rs:280-339`):
   - Thêm `must_not` + `has_id` (kể cả exclude numeric/string ids — mirror `PointId::sort_key`).
   - Parity target: mọi filter mà `cortex-sync/src/vector_sync.rs` emit (`stale_filter`
     :425-451 LUÔN có `must_not` + `has_id`) và reader filter (`project_scope_filter`
     must + match.any — red-team cleared: nằm trong capability `must` hiện tại).
   - Capture golden filters từ Python `qdrant_query_support.py` + `stale_filter` → fixture
     `filter_parity.json` (input filter + tập point id mong đợi match/exclude).
   - Delete path check: `delete` giữ `!point_matches` (`qdrant.rs:779-785`) — test end-to-end
     mức LocalClient rằng delete-with-filter giữ đúng điểm cần giữ.
2. **`get_collection_info` mang vector config** (`qdrant.rs:462-478`): trả shape REST-like
   `{"result": {"config": {"params": {"vectors": {"size" | {name: {"size"}}}}}}}` để
   `vector_store.rs::vector_sizes` dùng lại KHÔNG ĐỔI. Ghi divergence: distance/quantization
   không mô phỏng (JSON engine brute-force, không HNSW).
3. **Persist-mode bulk — diệt O(n²) GIỮA CÁC OPS** (red-team H4: mỗi upsert call ĐÃ flush
   đúng 1 lần; chi phí thật là ~70 calls/parser × re-serialize toàn store):
   - Thêm chế độ `persist=false` cho `upsert`/`set_payload`/`delete` (áp in-memory, không
     ghi file) + `flush()` public; caller (phase-02) gọi bulk trong pass và flush ĐÚNG 1
     lần cuối pass.
   - Crash-window: pass flush cuối → mất tối đa 1 pass (re-sync lại được, chấp nhận —
     ghi divergence).
4. **`create_payload_index` idempotent** (`qdrant.rs:869`): re-create cùng field không tích
   tuỷ duplicate.
5. **`LocalClient::open_readonly` — reader KHÔNG giữ lock** (red-team H3):
   - Không flock (writer `LOCK_EX|LOCK_NB` giữ nguyên → không bao giờcontended bởi reader;
     fail-fast same-instance sync giữ nguyên).
   - Đọc an toàn nhờ `write_atomic` (`util.rs:297-312` — temp + fsync + rename + dir fsync):
     reader mở file per-burst hoặc cached + revalidate mtime mỗi burst; file mất/đang thay
     → reload 1 lần trước khi trả lỗi.
   - Write ops trên read-only client = error trung thực (`read-only local client`).
6. **Shape-audit** (red-team M7 — phase-03 đang treo quyết định vào audit này):
   - Field `version` trên hit: QdrantLocal/QdrantLocal search trả shape gì mà sidecar
     forward (so `.venv` qdrant_client + `vector_worker.py`) → quyết định emit giả lập hay
     ghi divergence.
   - `QDRANT_HNSW_EF`: QdrantLocal có áp dụng không (đọc source `.venv`) → xác định inert
     trên local engine có phải divergence thật không.
   - Kết quả ghi `reports/phase01-engine.md`.

## Gates

- Unit tests: filter-parity fixture xanh toàn bộ (gồm case delete-giữ-đúng-điểm);
  collection-info shape khớp `vector_sizes` (anonymous `size` + named map); idempotent
  payload-index.
- Concurrent test: writer sync (LOCK_EX, persist bulk + flush cuối) song song reader
  (`open_readonly`) → không torn-read, không deadlock, reader thấy snapshot cũ-hoặc-mới.
- **Benchmark assertion (red-team H4 — không phải flush counter)**: upsert 9k điểm ×
  1024-dim qua 70 calls + 1 flush cuối: wall-time ghi số, tổng bytes serialized ≥ cỡ
  O(store size) (không O(n²)) — so trước/sau trong `reports/phase01-engine.md`.
- Audit output: `version` + `QDRANT_HNSW_EF` có kết luận trong report.
- Không regression: `cargo test -p cortex-storage` toàn xanh; lease/factory lane không đổi.

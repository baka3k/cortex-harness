# Phase 05 — Qdrant collection tuning + rebuild tooling (remote-mode value)

Goal: filter không quét full-scan và HNSW/quantization opt-in — **giá trị
thực chỉ materialize trên Qdrant server (remote mode)**. Gate đóng góp vào
G4 ở unscoped nhiều collection, điều kiện có remote store.

**Giới hạn local-mode (red team #3, kiểm chứng trên qdrant-client 1.18.0
`qdrant_client/local/qdrant_local.py`):** local mode **no-op** hóa
`create_payload_index` (:903-918 — "Payload indexes have no effect in the
local Qdrant"), `payload_schema` luôn `{}`, `LocalCollection` hardcode
HNSW m=16/ef_construct=100 và `create_collection` nuốt `**kwargs` im lặng.
Nghĩa là: trên store local, mọi thay đổi của phase này là call-through
vô hại nhưng không mang lại hiệu năng; local mode full-scan là thiết kế.
Acceptance đo hiệu năng **chỉ có ý nghĩa với remote**; acceptance local
chỉ assert call-through + không crash.

## Changes

### 1. Payload indexes cho field đã filter — `primary_vector_sync.py`

`_ensure_project_scope_index` (:197-207) hiện chỉ tạo index
`project_id_normalized`. Mở rộng tạo (idempotent, `wait=True`) thêm 3
keyword index: `parser`, `root_scope`, `file_path` — các field
`_delete_stale` filter (:228-237) dùng. Trên **remote** đây là build-index
online không cần rebuild collection; trên local client tự no-op (không
đổi gì). Chạy tự động ở lần sync kế tiếp.

### 2. HNSW / quantization opt-in lúc tạo collection — `local_qdrant.py`

`ensure_collection` (:167-188): forward `hnsw_config` /
`quantization_config` kwargs từ env (mặc định unset = giữ nguyên default):

- `QDRANT_HNSW_M`, `QDRANT_HNSW_EF_CONSTRUCT` → `HnswConfigDiff`.
- `QDRANT_SCALAR_QUANT=1` → `ScalarQuantization(int8, always_ram=true)`
  (mặc định tắt).
- Query-time `hnsw_ef`: `qdrant_query_support.search_collection` (Phase 04)
  đọc env `QDRANT_HNSW_EF` (unset → không gửi param, dùng server default).

Collection hiện hữu **không** tự đổi (ensure_collection raise khi vector
size khác — giữ nguyên); đường nhận config mới là script mục 3. Trên
local mode các kwargs bị client nuốt im lặng — thêm 1 dòng log warning
"tuning kwargs inert on local mode" khi phát hiện store là local để khỏi
gây ảo giác hiệu năng.

### 3. Rebuild script — `code-tiny/scripts/rebuild_vector_collection.py` (mới)

Copy-vector rebuild cho 1 collection (chủ định dùng cho remote; trên local
nó vẫn chạy được nhưng vô ích — in cảnh báo):

1. Scroll cũ `with_vectors=True` theo batch (net-new — repo chưa có flow
   này; qua `LocalQdrantStore.scroll` đã pass-through `with_vectors`).
2. Tạo collection `_rebuild_tmp` với tuning config từ env (mục 2).
3. Upload batch, đếm; **count validation**: điểm temp == điểm cũ (theo
   `count(exact=True)`).
4. Swap (không có alias): xoá collection cũ → tạo collection đích với
   tuning config → re-upload từ temp → **count assert lần 2 trên collection
   đích** (red team #12) → xoá temp. Bước destructive chỉ chạy với
   `--yes`; không có `--yes` thì dừng sau bước 3 và in kế hoạch. Khuyến
   nghị in help: backup trước khi chạy (`db_transfer export`).
5. Gọi `qdrant_layout_cache.invalidate(url)` (Phase 04).

## Tests

- `ensure_collection` forward kwargs (stub store); env unset → không gửi
  HNSW/quantization; local-mode store → warning inert (capture log).
- `primary_vector_sync`: stub `_LocalStore` assert 4 index được tạo,
  idempotent khi sync lần 2 (stub ghi nhận call; local-mode semantics
  không assert được trên stub — chỉ assert call-through).
- Rebuild script trên stub: copy đủ điểm, count mismatch (stub trả thiếu)
  → abort **trước** khi xoá; count assert lần 2 được gọi; không `--yes` →
  không đụng collection cũ.
- `hnsw_ef` chỉ được gửi khi env set (stub assert kwargs).

## Acceptance

- [ ] Local: sync chạy qua 4 index call không crash, warning inert xuất
      hiện đúng 1 lần; suite pass.
- [ ] **Remote (điều kiện có Qdrant server** — phối hợp plan
      `260818-infra-up-remote-support` nếu cần provision): sau sync,
      `get_collection_info.payload_schema` đủ 4 index (dump vào
      `reports/phase-05.md`); benchmark `unscoped-multi` --live --remote:
      p50 cải thiện thêm hoặc ngang Phase 04; `validate_retrieval.py` pass.
- [ ] Rebuild script dry-run (không `--yes`) trên 1 collection remote nhỏ
      chạy hết bước 1-3, count khớp; chạy `--yes` thật và benchmark
      trước/sau — số vào `reports/phase-05.md`.
- [ ] Nếu không có remote server trong môi trường hiện tại: các mục remote
      đánh dấu **deferred** trong report, phase vẫn ship phần call-through
      + script (không block phase 06).

## Rollback

Index: xoá index (chỉ tốn hiệu năng thêm, không sai kết quả). Env HNSW/
quantization: unset env. Script: file mới, không revert gì runtime.

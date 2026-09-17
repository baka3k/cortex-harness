# Phase 06 — Ingest: embedder reuse + batch defaults + upsert wait

Goal: giảm thời gian sync vector ở phía ingest. Gate: G6 (embed throughput
≥ 3× với default config; chốt lại sau Phase 01 nếu batch-1 short-text trên
MPS cho thấy 3× quá lạc quan).

**Sửa căn bản về durability (red team #8):** `sync_vector_documents`
upsert **trực tiếp vào collection đang sống** (`primary_vector_sync.py:330-332`)
— trên đường path này KHÔNG có staging gate; staged publication là thiết kế
của plan `260807-0929` (pending, chưa ship). Tức là partial visibility giữa
chừng sync đã tồn tại sẵn hôm nay, bất kể `wait`. Lý lẽ đúng cho wait=False
batch giữa là: **re-sync tự vá** — lần sync sau `_delete_stale` (:228-237)
+ re-upsert sẽ sửa batch mất; và kế hoạch này không làm tồi hơn hiện trạng.
Không viện dẫn staging publication.

## Changes

### 1. Embedder reuse — `code-tiny/tools/common/primary_vector_sync.py`

`sync_vector_documents` (:246-348) tạo `SentenceTransformer` mới mỗi call
(:286-296). Đổi default `embedder_factory` thành closure gọi
`embed_runtime.get_sentence_transformer(model_name, device)` (module Phase
02) — process-wide cache keyed `(model, device)`. `embedder_factory` param
(:265) giữ nguyên cho test inject. Process analyzer ngắn hạn không benefit
nhiều (1 call/process) nhưng doc-vectorize và tooling chạy nhiều sync/process
không còn load lại model.

### 2. Upsert wait policy — `primary_vector_sync.py` (:326-332)

Hiện `wait=True` cho **mỗi** batch. Đổi: batch thường `wait=False`, chỉ
batch cuối `wait=True` — sync return sau khi flush cuối cùng xong (ranh
giới durability của hàm giữ nguyên; crash giữa chừng để lại batch có thể
thiếu — chính sách tự vá là re-sync, đã là behavior hiện tại qua
`_delete_stale`). Không có test hiện tại nào assert per-batch `wait=True`
(stub chỉ ghi nhận calls `:42-43`, assert batch size `:208`) — phase này
là thay đổi contract duy nhất và test mới chốt contract tường minh.

### 3. Batch defaults

- dev-init config: `BATCH_SIZE` `"1"` → `"8"` (code `dev.py:2801`, doc
  `:2813`; MAX_EMBED_CHARS `"500"` ở `:2802`/`:2814` giữ nguyên — content
  shape, không phải perf).
- Analyzer argparse fallback: `EMBED_BATCH_SIZE` env else `8` (hiện là 4:
  python `:1918`, go `:1296`, java, js, php, vb; cplus đã là 8 `:6110` —
  liệt kê chốt trong PR).
- `qdrant_batch_size` 128 giữ nguyên.

## Tests

- `tests/test_primary_vector_sync.py`: assert contract wait tường minh —
  mọi batch trừ batch cuối được gọi `wait=False`, batch cuối `wait=True`
  (stub `_LocalStore` ghi nhận calls); embedder factory default không được
  gọi khi inject; inject qua `embedder_factory` vẫn priority.
- Test plumbing: dev-init config chứa `BATCH_SIZE=8`; env
  `EMBED_BATCH_SIZE` chảy tới analyzer CLI (mẫu test dev.py hiện có).
- Micro-bench throughput: timing inline trong test log hoặc tái dụng phần
  ingest của script Phase 01 — docs/s batch 1 vs batch 8 với stub encode
  sleep mô phỏng; số thật với model thật vào acceptance.

## Acceptance

- [ ] G6: throughput embed (docs/s) ≥ 3× so với `BATCH_SIZE=1` trên
      corpus synthetic với model thật — số vào `reports/phase-06.md`; nếu
      không đạt 3× do short-text, chốt số thực tế + đề xuất điều chỉnh
      default tiếp trong report.
- [ ] Sync giữa chừng kill-9 → lần sync sau tự vá (`_delete_stale` +
      re-upsert) — smoke test tay, ghi nhận vào report.
- [ ] Suite pass (đặc biệt `test_primary_vector_sync.py` cập nhật khớp).

## Rollback

Revert commit; config `BATCH_SIZE` quay về "1" bằng dev-init lại hoặc sửa
config — không đổi dữ liệu.

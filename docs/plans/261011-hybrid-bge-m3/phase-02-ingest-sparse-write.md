# Phase 02 — Ingest ghi dual-vector: schema + chokepoints + marker `hybrid` (code + doc)

> Tham chiếu: [plan.md](plan.md) AC3 · Prediction report risk H3 (mixed-state), Low (storage).
> Thiết kế schema: **giữ dense unnamed default** (zero-migration cho point cũ — Qdrant cho phép `vectors_config` default + `sparse_vectors_config` map cùng tồn tại; sparse luôn là named field `"sparse"`). Point cũ thiếu sparse → dense prefetch vẫn khớp (degrade trong Collection nội bộ, không error).

## Goal

Khi `EMBED_HYBRID=1`: mọi writer chokepoint ghi cả dense + sparse; collection mới/provision có `sparse_vectors_config`; sentinel `_embed_meta` stamp thêm `hybrid`. Flag tắt → byte-parallel hiện trạng.

## Changes

1. `code-tiny/tools/common/local_qdrant.py`:
   - `ensure_collection` (`:232`, `:272`): khi hybrid bật → truyền thêm `sparse_vectors_config={"sparse": qmodels.SparseVectorParams()}` **cho collection mới**.
   - **Hard constraint (digest, smoke-tested trên client 1.18.0 local mode):** KHÔNG thể thêm sparse vào collection đã tồn tại (`update_sparse_vectors_config` raise ValueError). `ensure_collection` gặp collection cũ thiếu sparse + flag bật → warning 1 lần chỉ đường sang backfill script (P4), **không** recreate tự động. Upgrade = recreate trong P4.
   - `QdrantWriter.upsert` (`:416`): nhận vector dạng dict `{"": dense_list, "sparse": SparseVector}` (digest xác minh: key `""` cho unnamed dense) — writer API mở rộng backward-compatible (truyền list như cũ vẫn chạy); `LocalQdrantStore` (`cortex_harness/storage/qdrant.py`) passthrough `**kwargs` — không cần sửa wrapper.
2. `code-tiny/tools/common/primary_vector_sync.py` (`sync_vector_documents` `:270`, encode `:319`): encode 1 lần, tách dense + sparse; `PointStruct.vector` thành named map khi hybrid bật.
3. 12 CodeEmbedder + cobol sync + livingdoc:delegate qua shared runtime — kiểm tra chỉ cần đi qua `primary_vector_sync`/cobol path không tự dựng PointStruct (cobol `tools/cobol/qdrant.py` dùng shared sync; livingdoc-vectorize dùng CodeEmbedder + writer chung). Rà 1 lượt, sửa chỗ nào tự dựng point.
4. Doc side: `doc-tiny/graphrag_ingest_langextract.py` `create_collection` (`:633`, `:677`) + upsert path — mirror schema + dual-vector; twin helper từ P1.
5. `code-tiny/tools/common/embedding_marker.py`: `sentinel_point()` thêm trường `hybrid: bool` — **visibility cho runbook/report thôi, KHÔNG phải nguồn chân lý capability** (digest xác minh sentinel upsert dạng list vẫn hợp lệ trong collection hybrid nên legality không đổi; capability query-side đọc từ collection info — P3). Reader `mismatch()` KHÔNG đổi — model identity vẫn là `BAAI/bge-m3` cho cả hai trạng thái (không tạo false-positive cho collection dense-only).
6. `cortex_harness/storage/remote_probe.py` `provision_qdrant_collection` (`:99-135`): khi hybrid bật → sparse_vectors_config trong create + stamp `hybrid`.
7. Wiring env: `dev.py` pass-through `EMBED_HYBRID` cho process env code + doc (pattern `EMBED_DEVICE`).

## Acceptance criteria

- AC3a: Collection mới tạo với flag bật có đúng 1 default dense (1024 COSINE) + sparse `"sparse"`; flag tắt → schema y hệt hiện trạng (byte-parallel).
- AC3b: Upsert hybrid ghi cả 2 vector; upsert legacy (list) không vỡ; `ensure_collection` trên collection cũ (thiếu sparse) + flag bật → warning chỉ đường, không mutate collection.
- AC3c: Marker stamp `hybrid=true` khi hybrid, `hybrid=false`/không có khi không; `check()` lộ cả hai trường; mismatch logic không đổi kết quả cho collection dense-only.
- AC3d: Collection `{base}_mess` (hash vectors) vẫn exempt như hiện trạng.

## Test scope (test-budget mapping)

- AC3a → 2 test (schema on/off); AC3b → 2 test (dual upsert round-trip trên FakeStore + idempotent ensure); AC3c → 2 test (stamp/read, mismatch không đổi); AC3d → 1 test exempt giữ nguyên; wiring env → 1 test.
- Kỳ vọng: ~8 test functions. Size **M**. Store thật chỉ chạy ở verification (P4) — unit test dùng FakeStore pattern sẵn có của `test_qwen3_embedding_marker.py`.

## Out of scope

- Không đổi query path (P3); không backfill điểm cũ (P4); không đụng graph DB/FalkorDB; không đổi `merge_hits`.

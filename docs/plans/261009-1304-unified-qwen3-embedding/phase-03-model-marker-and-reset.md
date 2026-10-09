# Phase 03 — Model-identity marker ở writer chokepoints + size guard + reset tooling

> Sửa theo red-team: F1 (marker phải nằm ở chokepoint mọi đường ghi, không chỉ primary sync), F2 (per-project reset phải dọn sentinel — nếu không pilot doc tự khoá chân mình), F7 (namespace collision + sentinel lọt kết quả search), F8 (soft warning phải nằm ở shared search_collection, không chỉ fastmcp_server).
> Tham chiếu luồng: [flows.md](flows.md) **§1 + §2** (toàn bộ đường ghi cần chặn: QdrantWriter, primary_vector_sync, cobol/qdrant, livingdoc, graphrag_ingest) và **§5 Ma trận tổng hợp** (đường ghi/đọc × cơ chế bảo vệ — checklist enforce).

## Goal

Biến "đổi model nhưng quên re-index" từ failure im lặng thành hard error (mọi đường ingest) / cảnh báo hiển thị (mọi đường query), sentinel không bao giờ lộ ra kết quả search, và có lệnh reset an toàn dọn đúng sentinel.

## Changes

1. **Sentinel module** `code-tiny/tools/common/embedding_marker.py`:
   - Point id `uuid5(PRIVATE_NAMESPACE, f"{collection}:embed-meta")` — `PRIVATE_NAMESPACE` là UUID hằng riêng, **không** `NAMESPACE_URL` (F7: python_analyzer.py:1097, graphrag_ingest_langextract.py:683 derive id thật từ NAMESPACE_URL).
   - Payload: `{"_embed_meta": True, "embedding_model", "vector_size", "project_id", "stamped_at"}`.
   - `stamp(...)` / `check(...) -> Optional[payload]` / `is_meta_point(payload)` helper.
2. **Enforce tại writer chokepoints (F1)** — check trước upsert/ensure, lệch model → raise kèm lệnh reset:
   - `code-tiny/tools/common/local_qdrant.py` `ensure_collection` (`:218-240`) — mọi analyzer writer + message_scan đi qua đây; **loại trừ collection suffix `_mess`** (hash vectors, message_scan.py:447).
   - `code-tiny/tools/cobol/qdrant.py:140-155` (sync riêng của cobol).
   - Livingdoc writers: `living-doc-vectorize.py:226-229`, `living-doc-vectorize-infra.py:165`.
   - Doc side: `create_collection` (`graphrag_ingest_langextract.py:611-620`) — thêm cả **size guard** (dim tồn tại ≠ dim embedder → raise kèm hướng dẫn `0_reset_all.py`) lẫn marker.
3. **Search exclusion + soft warning (F7, F8)** — trong shared `code-tiny/tools/common/qdrant_query_support.py` `search_collection` (mọi backend + parallel wrappers `cplus_mcp.py:935-949`, `android_mcp.py:754-768`, `java_mcp.py:603-617` dùng chung):
   - Filter `must_not` `_embed_meta` → sentinel không bao giờ là hit (unscoped query `project_scope.py:141-153`).
   - Đọc sentinel 1 lần/process/collection (cache): lệch model → gắn `"embedding_model_mismatch"` vào response, không fail.
   - Doc query path (`mcp_graph_rag.py`) thêm cảnh báo tương tự trong response meta.
4. **Reset dọn sentinel (F2)**:
   - `doc-tiny/0_reset_all.py` per-project path (`:57-72`, filter delete giữ collection) → xóa luôn sentinel point; marker check coi "collection chỉ còn sentinel" = rỗng → re-stamp thay vì raise.
   - `code-tiny/scripts/reset_code_collection.py` (mới, mirror 0_reset_all): `--project-id X` → drop `{X}`; `--collection C` override; `--include-messages` mới đụng `{X}_mess` (mặc định KHÔNG); `--dry-run`/`--force`; dùng storage adapter `delete_collection` (`cortex_harness/storage/qdrant.py:141-155`, factory `:85-93`).

## Acceptance criteria

- AC1: Upsert model mới vào collection marker model cũ → raise TỪ chokepoint tương ứng (local_qdrant / cobol / livingdoc / doc — parameterized 4 đường); chưa có marker → stamp + ingest OK.
- AC2: `_mess` không bao giờ bị check marker; lưu lượng hash vector không đổi.
- AC3: Sentinel không xuất hiện trong kết quả search (unscoped + scoped); query lệch model → response có `embedding_model_mismatch`, vẫn trả kết quả.
- AC4: Per-project doc reset xóa sentinel; sau reset, ingest model mới chạy sạch (không deadlock); dry-run không xóa gì.
- AC5: `reset_code_collection.py` drop đúng đích, `_mess` nguyên vẹn trừ khi `--include-messages`.

## Test scope (test-budget mapping)

- AC1 → 2 tests parameterized (raise/stamp-ok × chokepoint). AC2 → 1 test. AC3 → 2 tests (exclusion + warning). AC4 → 2 tests (reset clears sentinel; empty-with-sentinel re-stamps). AC5 → 2 tests parameterized (dry-run/force).
- Kỳ vọng: ~9 test functions. Size **L** (chạm 4 đường writer + shared search path).

## Out of scope

- Không wiring `dev sync code --reset` vào dev.py (coordinate 260821-2115 — runbook P5 dùng lệnh script trực tiếp); không migrate vector in-place.

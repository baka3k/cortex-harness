# Phase-01 — engine hardening (cortex-storage) — 2026-09-16

Plan: `plans/260916-1154-native-vector-ingest-local/plan.md` rev2, phase-01.
Scope: `rust/crates/cortex-storage/src/qdrant.rs` + `tests/parity.rs`.

## Delivered

| Gate (plan rev2) | Status | Evidence |
|---|---|---|
| Fix `point_matches`: `must_not` + `has_id` khớp shape `stale_filter` | ✅ | Full filter grammar: `must` / `must_not` (nested filters) / `should` + `min_should` / `has_id`. `stale_filter` shape `{"must": [...], "must_not": [{"has_id": [...]}]}` giờ giữ nguyên điểm keep — test `stale_filter_delete_honours_must_not_has_id` (tests/parity.rs) |
| `get_collection_info` mang vector config (REST-like) | ✅ | `result.config.params.vectors` — anonymous wrapper → bare VectorParams, named map giữ nguyên; `vector_sizes` (cortex-sync) ăn nguyên văn — test `collection_info_carries_vector_config_rest_shape` + matrix leg `drift/message-contract` |
| Persist-mode bulk (diệt O(n²) giữa các ops) | ✅ | `upsert_deferred` / `delete_deferred` / `flush()`; benchmark test `persist_mode_bulk_serializes_constant_volume`: 16 batch deferred + 1 flush serialize < 1/4 volume so với eager per-op (bytes + wall-time assertion, KHÔNG dùng flush counter — red-team H4) |
| `create_payload_index` idempotent | ✅ | Duplicate field_name collapse — test `payload_index_is_idempotent` |
| `open_readonly` (không lock, mtime revalidate) | ✅ | `LocalQdrantReader` — không flock, snapshot cache revalidate theo (mtime, len); concurrent test writer(sync)+reader(search) `concurrent_writer_and_lockfree_reader` — không torn-read, không deadlock |
| Legacy-guard compound key | ✅ | (`collection/` subtree OR root `storage.sqlite`) AND JSON absent → lỗi trung thực kèm recipe re-index; JSON present → cho phép coexist (cửa sổ re-index). Test `legacy_pickle_root_refused_loudly` |
| Filter-parity fixture với golden shape `stale_filter` | ✅ | 2 filter tests + delete-scenario trong parity.rs |
| Shape-audit | ✅ | Xem §Shape audit dưới |
| Benchmark assertion | ✅ | bytes-serialized qua per-store counter `flush_bytes_written()` + wall-time (eprintln trong test) |

## Kèm theo (phát hiện khi implement)

- **Bug normalize pre-existing**: `normalize_vectors_config` hiểu nhầm body
  `{"vectors": {"size": N, ...}}` (anonymous) thành named map với key
  "size"/"distance" → drift guard mất chiều. Đã sửa: có key `size` → anonymous,
  lưu dưới key `""` (F3b liên quan trực tiếp).
- **RSS parse**: path cũ parse store qua serde_json Value tree (≈5× kích thước
  typed ở scale 132MB) → overflow gate 300MB. Đổi sang typed streaming
  deserialization (`StoreFileDe` → typed points, vectors không bao giờ
  materialize thành Value tree). Reader peak đo được: **71MB trên store 118MB**.

## Shape audit (phase-03 gate input)

1. **`version` field**: sidecar/QdrantLocal hit = `{id, version, score, payload}`;
   native hit = `{id, score, payload, vector}` — **không có `version`** (engine
   JSON không track revision counter). Consumer trong `tools_semantic.rs` chỉ
   dùng `id/score/payload/_collection` → inert cho lane. Comparator phase-04
   bỏ qua `version` (accepted divergence, không fabricate giá trị giả).
   Lane post-strip thêm: bỏ `vector: null` + strip `text` khỏi payload
   (`lane_hit_shape`) để khớp `PayloadSelectorExclude(["text"])`.
2. **`QDRANT_HNSW_EF`**: python search truyền `search_params.hnsw_ef`
   (`qdrant_query_support.py:41-49`); sidecar đã ignore từ trước (không pass
   qua worker protocol); native search không có search_params → inert ở local
   mode. Matrix leg `tune-env/inert` chứng minh `QDRANT_HNSW_M/EF_CONSTRUCT/
   EF/SCALAR_QUANT` set → sync vẫn chạy, sizes nguyên, indexes đủ 4.
   **Documented divergence, không có hành động.**

## Engine tests

`cargo test -p cortex-storage` → 22 parity tests (7 mới) + 6 + 5, tất cả green.

# Phase 04: Local mode — vector sidecar `vector_worker.py`

## Mục tiêu

Làm cho **local Qdrant search chạy được từ Rust** mà không đụng format on-disk.
Kiến trúc chốt (plan.md D2): persistent Python sidecar đọc đúng store mà ingest Python
đang ghi — Rust chỉ nói NDJSON stdio.

## Scope

1. **`scripts/rust_mcp/vector_worker.py`** (mirror `embed_worker.py`):
   - Request: `{"op":"search", "collections":[...], "vector":[f32...], "limit":n,
     "filter":{...} | null, "with_payload":true}` và `{"op":"list"}`.
   - Response: `{"hits":[{"id","score","payload"}...]}` / `{"collections":[...]}` /
     `{"error":"..."}`; banner ra stderr, stdout protocol-only.
   - Import **chỉ** `qdrant_client` + numpy — cấm torch/transformers (contract test
     chặn bằng cách spawn worker với env chặn module hoặc đo import time).
   - Client per collection dir: `QdrantClient(path=<instance>/qdrant/<owner>)` — đúng
     cách `local_qdrant.py`/`doc_local_qdrant.py` làm; reuse `StorageLease` nếu cần.
2. **Rust side**: `Backend::LocalSidecar{worker}` trong trait `VectorSearch`:
   - Process manager persistent (pattern `cortex-mcp/src/mind/embed.rs:37-101`:
     spawn, NDJSON, timeout 120s, respawn 1 lần); env override
     `CORTEX_MCP_VECTOR_WORKER` (path worker) + `CORTEX_MCP_PYTHON`.
   - Lazy spawn ở request đầu (server khởi động nhanh như hiện tại); shutdown sạch khi
     server drop.
3. **Wire cả 2 plane**:
   - mind: thay nhánh lỗi `qdrant.rs:330-337` bằng LocalSidecar (giữ error envelope
     `project_not_registered` như hiện tại).
   - unified: `resolve_backend` mở rộng — `storage_backend=remote` → Remote; ngược lại
     → LocalSidecar (mirror `local_qdrant.py:90-113`, kể cả lỗi
     `RemoteQdrantUnsupportedError` cho URL http(s) truyền thẳng).
4. **Parity harness**: `compare_vector.py` chạy case local của phase-01 trên snapshot
   instance `cortex` (embedder onnx 2 phía, cùng file store, chỉ đọc).

## Gate

- [ ] Local case parity: 100% cấu trúc, score ≤ 1e-6, thứ tự hit khớp (≥ 40 case).
- [ ] Worker: import + sẵn sàng < 2s; RSS < 300MB; **0 import torch/transformers/sentence_transformers**
      (test assert theo stderr banner / trace).
- [ ] Đọc an toàn khi store đang được ghi: chạy worker song song với 1 sync nhỏ vào
      snapshot → không crash, không lock vĩnh viễn (SQLite WAL/readonly ok).
- [ ] Latency: local search p95 ≤ 50ms cho collection 8.6K points; end-to-end
      `semantic_search` unified (embed onnx + local search) p95 ≤ 150ms.
- [ ] Kill test: chết đột ngột của worker → request trả error envelope đúng, server sống;
      request sau respawn thành công.

## Ghi chú

- Nếu qdrant-client local search chậm không đạt ngưỡng (không có ANN): ghi số liệu,
  phương án B là precompute vector matrix trong worker memory (8.6K×1024 f32 ≈ 34MB —
  fits) và brute-force numpy — vẫn trong worker, contract không đổi. Decision ghi report.
- Rust không bao giờ ghi vào local store (read-only) — ingest vẫn là Python.

**Trạng thái 2026-09-15 — DONE.** Sidecar hoạt động end-to-end; parity trong report `reports/phase03-04-vector-lane.md`.

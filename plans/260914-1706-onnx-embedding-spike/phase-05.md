# Phase 05 — Sync-path decision + benchmark + docs + dogfood add-on

## Scope

Quyết định có số liệu (không có sẵn đáp án): **ingest-sync (#1, #2, #2b, #4) port sang
Rust embed hay giữ Python child vĩnh viễn?**

1. **Benchmark quyết định** (dùng số của phase-01):
   - Throughput embed CPU: ort vs SentenceTransformer/AutoModel torch, batch 8/32/128,
     text lengths thật (max 4000 / hard cap 16000).
   - Tổng thời gian embedding pass 1 kỳ sync stock (Python child hiện tại vs giả lập
     Rust embed cùng corpus).
2. **Nếu GO — port sync embed** (scope phái sinh, tách mini-phase 05B nếu làm):
   - Port phần cần thiết của `primary_vector_sync.py` vào `cortex-sync`/`cortex-embed`:
     `documents_from_payloads` (:99-155, secret-redact), bounded text, stale-point
     cleanup, upsert Qdrant (`local_qdrant` contract: COSINE, unnamed vector,
     `qdrant_batch_size` 128), cả **`_hash_vector` message fallback bit-replicate**
     (`message_scan.py:393-401`).
   - Cắm sau `Embedder` trait tại orchestrator embedding pass (#3) — child analyzer
     Python không còn chạy pass embedding khi `CORTEX_EMBED_BACKEND=onnx`.
   - Parity: qdrant point counts + point ids + vectors (cosine) khớp Python trên stock,
     full + incremental; summary JSON khớp schema.
3. **Nếu NO-GO**: giữ Python child, ghi decision record (lý do throughput/lactancy),
   cập nhật rust-full-migration để chính thức hoá "embedder là Python-sidecar vĩnh viễn
   cho sync; Rust chỉ native ở query/doc/mind".
4. **Docs + vận hành**: cập nhật `docs/cutover-runbook.md` (thêm flag
   `CORTEX_EMBED_BACKEND` + rollback), ReadMe/CLAUDE.md, `dev doctor` check hiển thị
   embed backend đang dùng.
5. **Dogfood add-on**: 1 kỳ sync + MCP đầy đủ với `CORTEX_EMBED_BACKEND=onnx` trên
   stock — không lỗi, counts khớp.

## Touchpoints inventory liên quan

#1, #2, #2b, #3, #4 (+ cập nhật trạng thái owner của mọi dòng trong bảng inventory
plan.md — checklist coverage phải 14/14 có owner chốt).

## Gates

- [ ] Benchmark report: số liệu đủ để chốt GO/NO-GO (không decide bằng cảm tính).
- [ ] (Nếu GO) parity sync stock: point counts + ids + cosine khớp, full + incremental.
- [ ] (Nếu GO) `_hash_vector` bit-replicate test (vectors so exact, không cosine).
- [ ] Coverage checklist 14/14 dòng inventory có owner chốt trong plan.md.
- [ ] Runbook + doctor + docs cập nhật; dogfood add-on PASS.

**Kết quả:** reports/phase05-sync-decision.md

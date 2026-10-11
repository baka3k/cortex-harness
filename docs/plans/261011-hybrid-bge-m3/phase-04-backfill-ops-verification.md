# Phase 04 — Backfill + ops: runbook A/B, reset tooling, docs sweep, verification

> Tham chiếu: [plan.md](plan.md) AC6, AC7 · Runbook gốc: `docs/UNIFIED_INGEST_QUERY_CONTRACT.md` mục "Embedding-model swap playbook (BGE-M3 era)".

## Goal

Collection dense-only đang sống có thể nâng cấp lên hybrid **không re-ingest**; operator có runbook bật/tắt/rollback; A/B gate chứng minh quality trước khi cân nhắc flip default. Plan đóng với flag mặc định vẫn OFF.

## Changes

1. **Backfill script** `code-tiny/scripts/backfill_hybrid_sparse.py`:
   - **Recreate-based (hard constraint local mode):** client 1.18.0 local mode KHÔNG thêm sparse vào collection có sẵn được → script: (1) export snapshot collection (payload + text qua `retrieve` paginate), (2) drop + create lại với `sparse_vectors_config`, (3) re-encode dense+sparse từ payload `text`/content rồi upsert lại, (4) stamp marker `hybrid=true`.
   - Đầu vào: `--project-id` / `--collection` (+ `--batch-size`, `--dry-run`, `--yes` cho drop — destructive nên luôn confirm trừ khi flag tường minh).
   - Dense được re-encode từ cùng model nên tương đương gốc (MPS nondeterminism chỉ lệch low-bit — chất lượng so bằng top-k overlap, không hash vector). Payload copy nguyên văn (hash check được).
   - Idempotent ở mức thao tác: chạy 2 lần = từ chối nếu collection đã hybrid (`--force` để ghi đè); checkpoint resume trong quá trình re-upsert (`--resume` ghi trạng thái local).
   - Doc side: script nhận `--doc-mode` (layout doc collection khác code) — một script, hai mapping payload.
2. **Reset tooling**: `0_reset_all.py` + `reset_code_collection.py` — collection drop nên tự nhiên sạch trạng thái hybrid; chỉ cần update stamp template + docs.
3. **Runbook** — mở rộng `docs/UNIFIED_INGEST_QUERY_CONTRACT.md` thêm mục "Hybrid retrieval enablement": (1) prefetch heads qua `prefetch_hybrid_heads.py`; (2) bật `EMBED_HYBRID=1` cho 1 project pilot; (3) backfill; (4) A/B bộ query EN/VI/JA + **identifier sets** (tên hàm/class, mã lỗi, serial — dense-only đang yếu nhất ở đây) so dense-only vs fused trên cùng collection; (5) rollback = tắt flag (không cần reset — collection hybrid vẫn query được dense-only).
4. **Docs sweep**: `.env.example` + `doc-tiny/.env-sample` thêm `EMBED_HYBRID`; README wiki 1 dòng; ghi chú storage overhead (số đo từ pilot, không phải estimate) vào runbook.
5. **Verification report** `verification-report.md` trong plan dir: A/B numbers, storage before/after, latency before/after, suite status.

## Acceptance criteria

- AC6: Backfill recreate trên collection pilot: collection sau backfill có sparse config; point upsert lại với dense+sparse; query fusion khớp được point cũ; payload hash trước/sao giữ nguyên (dense so bằng top-k overlap trên sample query, không hash vector — MPS nondeterminism); chạy lần 2 không flag `--force` = từ chối (idempotent guard).
- AC7: Runbook có đủ 5 bước + rollback không phá dữ liệu; flag default OFF được assert bởi test wiring (P2) — flip default là quyết định riêng post-A/B, không nằm trong plan này.

## Test scope (test-budget mapping)

- AC6 → 2 test script (round-trip backfill trên FakeStore/local qdrant temp; idempotent re-run) + 1 dry-run test. Size **S–M**.
- Runbook/docs → không unit test (docs-only); verification là hoạt động operator (gate thủ công theo runbook, ghi report).

## Out of scope

- Flip `EMBED_HYBRID` default (post-A/B); ColBERT; backfill tự động trong `dev sync` (chỉ script thủ công); hạ cấp hybrid → dense (không có use-case).

# Phase 05 — Backup/rollback + re-index runbook + A/B verification gate + docs sweep

> Sửa theo red-team: F9 (backup `db_transfer export` bắt buộc trước drop + rollback procedure), F6 (grep gate loại `*.bak`).
> Tham chiếu luồng: [flows.md](flows.md) **§5 Ma trận tổng hợp** — dùng làm checklist verify từng đường ghi/đọc sau re-index (mỗi hàng = một cặp before/after trong A/B report).

## Goal

Chuyển đổi vận hành an toàn và có đường lùi: backup trước — pilot giữa — cut over sau; tài liệu nhất quán; bằng chứng A/B trước khi tuyên bố xong.

## Changes

1. **Backup + rollback procedure (F9, D11)**:
   - Trước MỌI drop: `db_transfer export` collection tương ứng (repo khuyến nghị — `rebuild_vector_collection.py:21-22`). Ghi vị trí export vào runbook output.
   - Rollback nếu A/B gate fail: `db_transfer import` lại collection + set env `EMBEDDING_MODEL`/`CODE_EMBEDDING_MODEL` về model cũ + re-index ngược (model cũ vẫn có marker riêng nên chặn chính xác). Không xóa trắng trước khi có export.
2. **Runbook re-index** (ghi vào plan dir + Migration Playbook `docs/UNIFIED_INGEST_QUERY_CONTRACT.md:262-278`):
   - Chuẩn bị: tải snapshot `Qwen/Qwen3-Embedding-0.6B` local (hf-cli); `EMBEDDING_MODEL_PATH`/`CODE_EMBEDDING_MODEL_PATH` nếu dùng local.
   - **Pilot 1 project nhỏ**: doc — `db_transfer export` → `python doc-tiny/0_reset_all.py --project-id X --force` (đã dọn sentinel theo P3) → `dev sync doc all`; code — `db_transfer export` → `python code-tiny/scripts/reset_code_collection.py --project-id X --force` → `dev sync code`.
   - Gate đạt (mục 2) → lặp cho mọi project trong registry; `rm -rf .cortext-harness/sync-state/` nếu incremental state kẹt (HARNESS_WORKFLOW.md:477-483).
   - Ghi chú: user đã `dev init` từ trước cần re-init hoặc set env — config JSON cũ giữ model cũ.
3. **A/B verification gate** (pilot, ghi `verification-report.md`):
   - Bộ query cố định: ≥10 EN + ≥5 VI + ≥5 JA + ≥5 identifier/code — chạy `unified_mcp semantic_search` (code) + `mind_mcp semantic_search`/`hybrid_search` (doc) trước/sau, so tỷ lệ hit đúng kỳ vọng (metric "tỷ lệ" user từng dùng khi đánh giá bge-m3).
   - Benchmark có sẵn: `scripts/validate_retrieval.py` + `tests/benchmark_*.py` (prior-art plan.md:199-214) trước/sau.
   - **Cross-space check**: cosine(vector function ↔ vector doc paragraph) qua Qwen3 trên pilot — chứng minh mục tiêu thống nhất không gian đạt được.
   - Gate: tỷ lệ không thụt lùi so baseline; thời gian embed pilot trong ngưỡng chấp nhận trên MPS (ghi số đo).
4. **Docs sweep** (~20 hits, digest F1c): `code-tiny/README.md:168,213,244,267`; `code-tiny/Design.md:617`; `code-tiny/mcp/Readme.md:358`; `code-tiny/tools/vb/README.md:156`; `code-tiny/livingdoc/README.md:141,190,291,311,376` + `strategy.md:93,153,177,215`; `code-tiny/skills/code-graph-ingest/references/examples.md` (9 hits); `doc-tiny/Readme.md:48,61,62`; `docs/HARNESS_WORKFLOW.md:96`. Model cũ ghi là legacy option, không xóa khả năng override.
5. **Residual gate**: `rg "jina-embeddings-v3|bge-m3" -g '!*.bak' -g '!docs/plans/**'` → chỉ còn policy legacy/tests cố ý/docs legacy-option. Ghi kết quả vào verification-report.

## Acceptance criteria

- AC1: verification-report.md có bảng A/B (trước/sau × EN/VI/JA/code) + cross-space cosine demo + số đo thời gian + vị trí backup export.
- AC2: Runbook tái lập được bởi người khác; rollback procedure thử được trên pilot (ít nhất liệt kê lệnh chính xác).
- AC3: Residual grep gate đạt (loại `*.bak`); docs không còn model cũ như default.

## Test scope (test-budget mapping)

- Phase vận hành + tài liệu: không thêm unit test (AC1-AC3 là bằng chứng scriptable). Size **M**. 1 script A/B nhỏ nếu cần tự động hóa bộ query (tùy chọn).

## Out of scope

- Không re-index toàn bộ nếu pilot chưa đạt gate — dừng, báo cáo, chờ quyết định.

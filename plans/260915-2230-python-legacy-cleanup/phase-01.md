# Phase 01 — Disposition audit: bảng giữ/xoá/forced toàn bộ .py

## Mục tiêu

Trả lời chính xác cho từng file `.py` trong repo: **XOÁ / GIỮ (forced-Python) / GIỮ (parity infra) / FIXTURE** — kèm bằng chứng path:line. Đây là input bắt buộc của phase 02–04; không xoá file nào nằm ngoài bảng này.

## Việc

1. **Enumerate spawn sites** (nguồn chân lý "còn live"):
   - Rust: `grep -rn "\.py" rust/crates --include="*.rs"` — đã biết sẵn: `cortex-sync/src/orchestrator.rs:2935` (incremental_sync.py delegation), `:276/:343` (delegate reasons), `cortex-dev/src/cmds/harness.rs` (orchestrator.py, context_selector.py), `cortex-embed/src/sidecar.rs` (embed_worker.py), GLiNER runtime = inline `python -c` trong `cortex-doc/src/providers.rs:153-186` (đính chính: `mind/qdrant.rs` KHÔNG spawn gliner_sidecar — file đó chỉ là parity tool), `cmds/docsync.rs` (graphrag_ingest_langextract.py), journal consumer spawn trong sync.
   - `DELEGATE_SENTINEL`: tìm mọi sender (children gửi sentinel về orchestrator) → liệt kê lane nào còn bounce về Python.
   - Makefile (`Makefile:180-203` parity targets, `LIFECYCLE`/`DEV` — xác nhận đã binary-only), `.github/workflows/*`, `installers/`, `dev.sh/bat/ps1`, `dev-global.cmd`, wrapper/inno/scoop.
   - Docs khai báo usage: `docs/cutover-runbook.md`, `ReadMe.md`, `INSTALLER_GUIDE.md`, `skills/**`.
2. **Phân loại** mỗi file .py vào 4 nhóm (bảng ở plan.md Overview). Quy ước:
   - File bị import bởi nhóm forced/parity → kế thừa nhóm của importer.
   - Test suite chỉ import runtime đã chết → XOÁ; test chỉ tiêu thụ fixture JSON → giữ nguyên nhóm fixture.
   - Mơ hồ → mặc định GIỮ + đánh dấu `needs-decision` kèm câu hỏi cụ thể.
3. **Smoke thực nghiệm** để xác nhận delegate path sống/chết:
   - 3 kỳ `dev sync code/all` trên stock project: log có dòng `[cortex-sync] python-plane delegation` không?
   - `dev sync doc`, `dev harness`, `dev mcp start` (unset env): record binary nào chạy.
   - Kết quả ghi `reports/smoke-delegation.md` — đây là gate của phase-03.
4. Viết **`reports/disposition.md`**: bảng per-file/per-glob + script `scripts/audit_python_disposition.sh` (grep audit tái chạy được, exit non-zero khi thấy file ngoài keep-list).

## Gate / Verification

- `reports/disposition.md` cover 100% file .py (script đếm khớp `find -name "*.py"`).
- Mọi dòng XOÁ có ≥1 bằng chứng "không còn referencer"; mọi dòng GIỮ có ≥1 referencer sống hoặc decision trích plan umbrella.
- Smoke report có kết quả sync.

> **Deviation-accepted (2026-09-15, sau review):** gate gốc quy định "3 kỳ sync smoke" nhằm
> chứng minh delegate path CHẾT để mở nhánh xoá phase-03. Thực tế 2 run đầu đã quyết định
> ngược lại (delegation SỐNG trên ladybug local — `reports/smoke-delegation.md`); run 3 đòi
> bootstrap store thật, không tăng thông tin cho disposition → bỏ, phase-03 khoá nhánh B.
> `dev sync doc` / `dev harness` / `dev mcp start` không smoke trực tiếp (tránh đụng state
> instance user) — thay bằng code-evidence có path:line trong disposition.md. Chấp nhận
> bởi orchestrator; reviewer đã review rationale (finding #8, verdict chấp nhận được).

## Rollback

Phase chỉ đọc + viết reports — không có rollback cần thiết.

# Phase 03 — Retire sync-plane remnants (incremental_sync + tools/{common,graph})

## Mục tiêu

Chấm dứt Python delegation path trong cortex-sync NẾU smoke chứng minh không lane nào còn dùng; xoá phần `code-tiny/tools/` chỉ còn phục vụ path đó. Nếu còn live → chốt forced-Python, KHÔNG xoá (plan không port thay thế).

## Tiền đề

- Phase-02 đã xoá query-plane (giảm mặt bằng import).
- `reports/smoke-delegation.md` (phase-01): 3 kỳ sync smoke — nếu **không một dòng** `[cortex-sync] python-plane delegation` và không child gửi `DELEGATE_SENTINEL` → path chết, xoá được.

## Việc (nhánh A — path chết, nhánh mặc định)

1. Xoá `delegate_to_python` + `DELEGATE_SENTINEL` handling trong `rust/crates/cortex-sync/src/orchestrator.rs:276,343,2935` → thay bằng hard error có hint (graph target resolution fail = lỗi cấu hình, phải loud).
2. Xoá per disposition: `code-tiny/tools/sync/**` (incremental_sync.py + maps/matrix mirror), phần `tools/common/**` + `tools/graph/**` không được keep-list tham chiếu. **Keep bắt buộc**: những gì journal consumer (`python -m tools.graph.journal.consumer` nếu còn live) và cplus clang plane import — phase-01 đã liệt kê.
3. Quyết journal consumer theo disposition: nếu cortex-sync journal recovery đã tự xử lý → xoá cả consumer; nếu không → giữ, ghi forced-Python.
4. Cập nhật runbook mục sync (mô tả delegation path cũ), CI sync smoke jobs.

## Việc (nhánh B — path còn live)

1. KHÔNG xoá; mở plan riêng "port sync delegation lane sang Rust" nếu user muốn zero-Python.
2. Ghi forced-Python inventory vào runbook + `reports/final-sweep.md` (phase-05), kèm trigger condition chính xác (reason string của delegate).

## Gate / Verification

1. Nhánh A: sync smoke full-matrix (code/all/doc × incremental/full) — mọi lane binary, summary khớp baseline phase-07 analyzer cutover.
2. Grep audit: `delegate_to_python|DELEGATE_SENTINEL|incremental_sync` trong rust/ = 0 match (nhánh A).
3. `cargo build` + clippy sạch; parity sync-suite còn sống chạy xanh.
4. Commit riêng + tag `python-cleanup-sync-plane`.

## Rollback

`git revert` tag. Nhánh B không có xoá — chỉ docs.

# Phase 03 — Verify + đo trên corpus legacy + docs

> Gate ra: M6 (số đo before/after thật trên corpus lớn)

## Tasks

### 3.1 Đo trên corpus legacy lớn (không commit corpus)

- Chuẩn bị manifest 230-file (thư mục legacy thật của user, KHÔNG copy vào repo):
  - Baseline: jar trước plan (LL-only) — lưu jar cũ `vb6-antlr-worker.jar.pre-sll`
    hoặc tắt SLL bằng `VB6_WORKER_SLL=0` trên jar mới.
  - SLL-first: jar mới.
  - Mỗi cấu hình chạy 2 lần, lấy run thứ 2 (JIT warm); ghi
    `worker_meta.elapsed_ms`, `parse_ms_total`, `sll_fallback_files`.
- Kết quả viết `verification-report.md` (bảng before/after, tỉ lệ fallback,
  file chậm nhất trước/sau).

### 3.2 Quyết định go/no-go dựa trên R3

- Nếu `sll_fallback_files / N > 30%` hoặc two-stage chậm hơn baseline →
  mặc định `VB6_WORKER_SLL=0` trong adapter (feature vẫn ship, tắt mặc định)
  và ghi rõ vào report + plan.md (status `complete-with-exclusions`).
- Ngược lại: giữ mặc định bật; cập nhật `plan.md` status.

### 3.3 Docs + log

- `docs/logs/2026-10-02-vb6-worker-progress-sll.md` (theo pattern hi-log):
  tóm tắt motivation (jstack evidence), quyết định AD-01..AD-06, số đo M6.
- Cập nhật `code-tiny/tools/vb/README.md` phần worker: dòng stderr protocol
  `[vb6][worker] parsed i/N ...`, env `VB6_WORKER_SLL`, meta mới.
- Kiểm tra `docs/plans/261002-1410-vb6-worker-progress-sll/plan.md` status
  transition (`planned` → `complete` / `complete-with-exclusions`).

### 3.4 Verification checklist

- [x] verbose run end-to-end trên corpus lớn: thấy progress live, summary
      slowest in đúng.
- [x] Không dòng stderr nào của worker rơi vào stdout JSON.
- [x] Timeout path vẫn hoạt động: batch lớn + `--workspace-timeout-ms` nhỏ →
      allFailed JSON + JVM exit ngay (regression test daemon fix cũ)
      — 228-file manifest + 500ms timeout: rc=0, 228/228 ok=false, JVM exit 0.56s.
- [x] Full `-k vb6` pass lần cuối.

## Định nghĩa xong

- [x] verification-report.md có số đo before/after + tỉ lệ fallback
- [x] Go/no-go SLL quyết định + ghi vào plan.md + README
      (NO-GO: fallback 33.3% > 30% → adapter mặc định `VB6_WORKER_SLL=0`)
- [x] Session log + index entry (hi-log pattern)
- [x] Full vb6 suite pass

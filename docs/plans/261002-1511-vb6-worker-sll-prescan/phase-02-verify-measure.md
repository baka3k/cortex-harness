# Phase 02 — Verify + đo 2 corpus + docs

> Gate ra: M2 (corpus legacy hoàn tất trong budget), M5 (số đo trước/sau
> trong verification-report.md)

## Tasks

### 2.1 M1 single-file proof (không commit corpus)

- Manifest 1 file: file .frm lớn nhất của legacy corpus
  (`<LARGEST_FILE>.frm`, ~20k dòng) từ `<CORPUS_DIR>` (điền path cục bộ
  lúc chạy, không commit path thật), materialize `.frm`→`.cls`
  như adapter.
- Trước fix (đã đo 2026-10-02): timeout 120s, 0 progress lines.
- Sau fix: wall < 10s kỳ vọng, stderr có `scanning 1/1` → `parsing 1/1` →
  `parsed 1/1`; ghi vào verification-report.

### 2.2 M2 legacy corpus end-to-end (2 cấu hình)

- Corpus: `<CORPUS_DIR>` (legacy corpus, hàng trăm file / ~400k dòng;
  manifest build bằng `materializeDesignerModule` như adapter).
- Chạy qua ADAPTER (giống user thật — env `VB6_WORKER_SLL=0`) và jar-direct
  (default SLL ON) — mỗi config 1 lần warm: ghi wall, `parse_ms_total`,
  `sll_fallback_files`, ok/failed, top-10 slowest (stderr rows), số dòng
  scanning/parsing/parsed.
- Đo thêm pre-pass wall riêng: timestamp giữa start worker và dòng
  `scanning N/N` → `parsing 1/N` (đọc stderr capture).
- Nếu parse loop (LL) chậm trên cùng các file đó (R4): ghi bảng file + ms
  làm bằng chứng follow-up; KHÔNG đổi default trong plan này.

### 2.3 Sample legacy corpus regression

- Chạy qua adapter, so với số 261002-1410 (parse_ms_total ≈ 0.8–1.0s,
  228 file): phải cùng cỡ; scanning lines xuất hiện; không payload drift
  (ok/failed counts khớp).

### 2.4 verification-report.md (trong plan dir này)

- Bảng before/after: pre-pass wall (∞/timeout → X s), end-to-end wall, slowest
  per file, sll_bail rate trên corpus legacy (data cho follow-up SLL).
- Ghi chú semantics `error_nodes` 0/1 (AD-05) + R1/R2 accepted trade-offs.
- Re-grep consumer `error_nodes`/`has_error` lần cuối (verify research §2
  vẫn đúng trước khi commit).

### 2.5 Docs + log

- `docs/logs/2026-10-02-vb6-worker-sll-prescan.md` (pattern hi-log):
  motivation (jstack + single-file repro), AD-01..06, số đo M2/M5.
- `code-tiny/tools/vb/README.md`: bổ sung `scanning` vào stderr protocol
  nếu mục worker có mô tả (kiểm tra mục đã có từ plan 261002-1410 chưa).
- `plan.md` status → `complete` / `complete-with-exclusions` + reason.

### 2.6 Verification checklist

- [x] Full `-k vb6` pass lần cuối (ignore file mcp hỏng sẵn) — 147 passed
- [x] stderr không rò vào stdout JSON (test_stdout_contract_untouched)
- [x] Timeout path regression: manifest 263 file + 500ms budget → rc=0,
      allFailed, wall <1s (daemon fix cũ giữ vững)
- [x] No test asserts exact stderr line COUNT → đã kiểm (research §4)

## Test scope

- Mapped AC: M2, M5.
- Expected size: **L** — đo scripted trên 2 corpus thật + docs; không commit
  corpus, chỉ commit số đo.

## Định nghĩa xong

- [x] verification-report.md có bảng before/after cả 2 corpus
- [x] M1/M2 gates đạt bằng số liệu thật (M2 pass ở cấu hình SLL; cấu hình
      adapter-default LL = R4 evidence, treo tại file form ~6.9k dòng)
- [x] Session log + README cập nhật
- [x] plan.md status transition + cross-ref 261002-1410 (R4 follow-up)
- [x] Sample corpus: bỏ điều tra sâu 11-file flip theo chỉ đạo user 2026-10-02
      (MSDN fragment fixtures, đúng H1 — ghi nhận trong report, khuyến nghị
      ignore-folder thư mục fixture của parser trong corpus)

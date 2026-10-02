# Red Team — 261002-1410-vb6-worker-progress-sll

Self adversarial pass (mode `--full`, red-team optional → 1 pass inline).
Each hypothesis: attack surface, disposition.

## H1 — stderr protocol nhiễu

- **Attack**: `ignoreSyntaxErrors=true` để nguyên ConsoleErrorListener →
  stderr có dòng `line x:y …` lẫn giữa progress lines; regex adapter
  `file=(\S+)` cũng vỡ với file name chứa khoảng trắng (corpus thật có).
- **Disposition (mitigated)**:
  - Prefix bắt buộc `[vb6][worker]`; adapter chỉ parse dòng khớp format;
  - Format sửa thành `file=<name> ms=<n>` với `file` ở giữa và `ms` neo cuối:
    regex `^Parsed file=(.*) ms=(\d+)( sll_fallback)?$` — `(.*)` cho phép
    khoảng trắng. Phase-01 đã cập nhật.

## H2 — Popen relay deadlock

- **Attack**: đọc stderr line-by-line trong khi java ghi đầy stdout pipe buffer
  (~64KB JSON!) → java block ghi stdout → deadlock chung.
- **Disposition (mitigated, bắt buộc)**: drain stdout bằng thread riêng vào
  buffer; thread chính đọc stderr. Phase-01 1.2 ghi rõ là không-optional.

## H3 — SLL bail trên file lỗi cú pháp thật

- **Attack**: corpus legacy có file cú pháp lỗi thật → SLL bail → parse 2 lần
  (SLL+LL) → tệ hơn LL-only; nếu nhiều file vậy, feature 2 phản tác dụng.
- **Disposition (measured)**: counter `sll_fallback_files` + go/no-go 30% ở
  Phase 03; escape hatch `VB6_WORKER_SLL=0` giữ feature rollback-able mà không
  revert code.

## H4 — Retry path tính timing 2 lần

- **Attack**: batch fail → retry `analyzeFiles(retry)` → parseFile chạy lại →
  timing map ghi đè; meta `parse_ms_total` cộng dồn 2 lần nếu cộng thẳng.
- **Disposition (accepted)**: timing lấy giá trị lần cuối (map put ghi đè);
  `parse_ms_total` cộng từ map sau batch cuối, không cộng qua 2 lần chạy.
  Phase-01 1.1 đã ghi chú.

## H5 — jstack evidence là anegdote

- **Attack**: 1 lần quan sát ALL(*) hotspot không chứng minh SLL cải thiện —
  SLL có thể chậm hơn nếu fallback cao.
- **Disposition (measured)**: M6 bắt buộc số đo before/after trên corpus thật,
  2 run/config lấy run warm; không ship mặc định bật nếu không có số.

## H6 — worker_meta mở rộng vỡ downstream

- **Attack**: consumer python/journal parse chặt meta shape → thêm field vỡ.
- **Disposition (checked)**: contract test chỉ spot-check key; `worker_meta`
  truyền qua như free-form dict; thêm field an toàn. Verify lại ở Phase 01
  (test assert key-specific, không exact-shape).

## H7 — Duplicate vendor tail drift

- **Attack**: override `parseCode` phải copy ~30 dòng vendor (lexer→parser→
  listeners→startRule→moduleName→visitor); vendor bump version sau này →
  copy lecial cũ, ngữ nghĩa lệch âm thầm.
- **Disposition (accepted + documented)**: VENDOR.md pinned commit; copy ghi
  chú "mirror of vendor <commit>, keep in sync"; khi bump vendor phải re-diff
  tail. Không chọn sửa vendor source vì tránh fork diff khi resync.

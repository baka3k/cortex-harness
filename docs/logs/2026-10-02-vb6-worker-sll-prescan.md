# 2026-10-02 — vb6-worker SLL pre-scan: bỏ vách đá LL trong tiền-xử lý + scanning progress

Plan: `docs/plans/261002-1511-vb6-worker-sll-prescan/` · Verify:
`verification-report.md` trong plan dir · Status: plan → `complete-with-exclusions`

## Motivation

Sync ~230-file legacy (corpus legacy chính — đường dẫn cục bộ, không commit;
hàng trăm file / ~400k dòng) "treo" vô hình hàng chục phút. jstack
(2026-10-02) chỉ ra hang thật nằm ở **tiền-xử lý của worker** —
`Vb6Worker.countSyntaxErrors` (:261) parse NGẦM toàn bộ corpus một lượt nữa
bằng ANTLR **LL mặc định** trước khi vòng parse chính bắt đầu: 1 file ~19.6k
dòng tự exceed 120s; run full-corpus 540s không in nổi 1 dòng log. Tối ưu
SLL hai-giai-đoạn của plan 261002-1410 không chạm được đoạn này (pre-pass đi
trước và không đọc env).

## Thay đổi (worker, 1 commit)

- `WorkerRunner.createSllParser` (static, package-private): recipe SLL +
  `BailErrorStrategy` + **bail khi SLL leo lên full-context simulation**
  (`reportAttemptingFullContext` → `ParseCancellationException`) — đúng chỗ
  sinh vách đá `closure_`. `parseStageSll` gọi lại helper (hết trùng recipe).
- `countSyntaxErrors` → SLL qua helper: sạch → 0, bail/throw → 1. Consumer
  chỉ branch 0 vs >0 (`has_error`, retry-set, message) nên exact LL count
  đổi thành 0/1 (`error_nodes` AD-05) — không test/consumer nào đọc đếm.
- Pre-pass in `[vb6][worker] scanning i/N file=<rel>` trước mỗi count —
  tiền-xử lý hết vô hình; adapter relay tự động (prefix match).

## Số đo (chi tiết trong verification-report.md)

| Case | Trước | Sau |
|---|---|---|
| Form lớn nhất corpus (~19.6k dòng) đơn lẻ | timeout 120s, 0 dòng log | **2s**, ok=true, có scanning/parsing/parsed |
| Corpus legacy chính, SLL two-stage | — | **53s wall**, đủ rows, slowest 2.8s |
| Corpus legacy chính, default adapter (LL loop) | treo ở pre-pass | pre-pass ~5s NHƯNG **loop LL treo tại 1 file form ~6.9k dòng** (R4) → timeout 600s, 0 ok |
| Corpus mẫu 228 file | ~1s | 2.5s wall, không drift (trừ 11 MSDN-fragment fixture flip theo H1 — chấp nhận, khuyến nghị ignore thư mục fixture của parser trong corpus) |
| Timeout path (500ms budget) | — | rc=0, <1s, allFailed — daemon exit fix giữ vững |

Tests: `test_vb6_worker_progress.py` + contract **38 passed**; full
`pytest -k vb6` **147 passed**.

## Quyết định + follow-up

- AD-06 giữ nguyên default `VB6_WORKER_SLL=0` (không relitigate go/no-go cũ).
- **R4 evidence mới**: trên corpus legacy chính, chỉ SLL hai-giai-đoạn hoàn
  tất được (53s); LL loop treo ở 1 file form ~6.9k dòng. Follow-up (1 dòng
  adapter + flip test, chờ duyệt): reconsider default sang SLL cho corpus
  kiểu này, hoặc set `VB6_WORKER_SLL=1` khi sync corpus này.
- Chấp nhận H1: SLL có thể false-error trên fragment (11 fixture MSDN) —
  chỉ ảnh hưởng message/flags, không mất payload.

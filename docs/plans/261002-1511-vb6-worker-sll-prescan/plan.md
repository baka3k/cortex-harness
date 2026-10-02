---
title: "vb6-worker: SLL pre-scan (countSyntaxErrors) + pre-pass progress lines"
status: complete-with-exclusions (pre-pass SLL + scanning lines shipped;
R4 follow-up: parse loop LL mặc định vẫn treo trên corpus legacy chính — evidence
trong verification-report.md, chờ quyết định reconsider VB6_WORKER_SLL)
created: 2026-10-02
mode: hi-plan --full
scope: >
  Eliminate the hidden LL parse cliff in the worker pre-pass: countSyntaxErrors
  (Vb6Worker.analyze pre-loop) re-parses EVERY file in default LL mode before
  the batch parse loop starts — proven to burn minutes per pathological file
  (live jstack + single-file repro on 2026-10-02). Replace it with the SLL +
  BailErrorStrategy recipe (bail on syntax error AND on full-context
  fallback), emit "[vb6][worker] scanning i/N file=<rel>" progress lines for
  the pre-pass, and measure the real legacy corpus end-to-end.
  OUT of scope: vendor sources, VB6_WORKER_SLL batch two-stage default (the
  M6 NO-GO of plan 261002-1410 stays), parse cache/parallelization, stdout
  JSON protocol, adapter relay changes (scanning lines relay automatically).
blockedBy: []
blocks: []
relatedPlans:
  - docs/plans/261002-1410-vb6-worker-progress-sll
sources:
  - code-tiny/tools/vb/antlr_worker/worker/src/main/java/io/cortex/vb6/worker/Vb6Worker.java
    (pre-pass loop :238-267; countSyntaxErrors :2080-2105; syntaxErrors
    consumption :307,:315,:375,:381-383,:391-393,:672-673)
  - code-tiny/tools/vb/antlr_worker/worker/src/main/java/io/cortex/vb6/worker/WorkerRunner.java
    (SLL stage recipe parseStageSll :206-216; PROGRESS_PREFIX :44)
  - Live evidence 2026-10-02 (this session):
    jstack of a full-corpus run: vb6-analyze thread CPU-bound for its whole
    wall time inside ParserATNSimulator.closure_, bottom frames
    Vb6Worker.countSyntaxErrors → startRule; stderr had ZERO lines (pre-pass
    runs before the first progress line).
    Single-file repro: `<LARGEST_FILE>.frm` (~20k lines, largest file of
    corpus `<CORPUS_DIR>`) alone burns the whole 120s workspace budget in
    the pre-pass → timeout, 0 progress lines.
    Contrast: small 194-file control corpus (18.6k lines) pre-pass+parse = 3s.
---

# vb6-worker: SLL pre-scan (countSyntaxErrors) + pre-pass progress lines

## Overview

Plan 261002-1410 shipped per-file progress + two-stage parsing, but the
legacy corpus (`<CORPUS_DIR>`, hàng trăm file / ~400k dòng) still
"hangs" invisibly. Root cause found 2026-10-02 (jstack + single-file repro):
`Vb6Worker.analyze()` runs a **pre-pass** over every entry BEFORE the batch
parse loop — `entry.syntaxErrors = countSyntaxErrors(entry.content)` performs
a FULL ANTLR parse per file in **default LL mode**, with no SLL, no bail, no
deadline. On big/complex files (nhiều file >5k lines, largest ~20k) LL
prediction collapses into `closure_` recursion: one file alone exceeded a
120s budget; a whole-corpus run never emitted a single progress line before
timeout
(the pre-pass precedes the first `parsing i/N` line).

Key safety property that makes a cheap fix acceptable: `syntaxErrors` does
NOT gate the primary batch — the batch includes every readable file
(Vb6Worker.java:271-284). It only feeds (a) the batch-failure retry
exclusion set (:307), (b) error-message strings (:381-393), and (c)
`parse_meta.has_error` / `error_nodes` (:672-673). So an approximate count
(0 vs ≥1) preserves every functional behavior; only `error_nodes` granularity
changes (exact LL count → 0/1).

### Coverage matrix

| Cần biết | Evidence |
|---|---|
| Pre-pass loop per-entry costs: read + decode + (utf8 copy) + second read + name regex + **countSyntaxErrors** | Vb6Worker.java:238-267 (agent report §8) |
| countSyntaxErrors = full LL parse, counting listener, catch Throwable → 1 | Vb6Worker.java:2080-2105 |
| syntaxErrors consumed at 6 sites incl. `has_error`/`error_nodes` payload flags | :307,:315,:375,:381-383,:391-393,:672-673 (agent report §1) |
| No test asserts the error strings, count values, `has_error`, `error_nodes` (VB6) | agent report §2; test_vb6_antlr_worker_contract.py:79-86 only ok/ok |
| Primary batch includes all readable files regardless of syntaxErrors | Vb6Worker.java:271-284 (agent report §8) |
| `parsing`/`parsed` progress only exists in WorkerRunner.parseFile — pre-pass prints nothing today | WorkerRunner.java:113,140-148 |
| Adapter relays any `[vb6][worker]`-prefixed stderr line live without verbose; `_PROGRESS_LINE_RE` only matches ` parsed ` → scanning lines can't pollute summary | vb6_antlr_adapter.py:333-341,45-47 (agent report §5) |
| No existing test breaks when a third stderr line kind appears | agent report §4 (test-by-test analysis) |
| WorkerRunner.parseStageSll has no instance state → refactorable to shared static | WorkerRunner.java:206-216 (agent report §7) |
| PROGRESS_PREFIX is package-private static, same package as Vb6Worker | WorkerRunner.java:44 |
| M6 NO-GO: two-stage batch parse ships OFF (`VB6_WORKER_SLL=0` via adapter); measured on the sample legacy corpus (228 files) (parse_ms_total ≈ 1s — NOT the cliff corpus) | plan 261002-1410 verification-report.md; vb6_antlr_adapter.py:303-307 |
| Pre-pass is independent of VB6_WORKER_SLL (it predates WorkerRunner and never consulted the env) | Vb6Worker.java:261 vs WorkerRunner SLL_ENABLED |

### Scope Challenge (3 câu)

1. **Làm gì?** Bỏ vách đá LL trong tiền-xử lý worker: `countSyntaxErrors`
   parse SLL + Bail (bail thêm ở full-context fallback — đúng chỗ sinh vách
   đá) và in progress `[vb6][worker] scanning i/N file=…` cho tiền-xử lý, để
   corpus legacy hết "treo vô hình" và đo được thời gian từng pha.
2. **Trong/ngoài scope?** Trong: worker pre-pass (SLL count + bail-on-full-
   context + scanning lines), shared SLL helper (không drift recipe), stderr
   protocol doc, tests worker + adapter-relay coverage. Ngoài: vendor,
   đổi default `VB6_WORKER_SLL` (go/no-go 261002-1410 giữ nguyên), parse
   cache, đa luồng, stdout JSON protocol, adapter relay logic.
3. **Khi nào xong?** (a) `<LARGEST_FILE>.frm` đơn lẻ chạy xong trong ~vài
   giây với scanning lines nhìn thấy (không còn timeout 120s); (b) legacy
   corpus end-to-end hoàn tất trong workspace budget với số đo
   trước/sau trong verification-report.md; (c) `-k vb6` xanh; (d) không test
   contract nào đổi kỳ vọng stdout.

### Architecture Decisions

| AD | Quyết định | Lý do |
|----|-----------|-------|
| AD-01 | `countSyntaxErrors` → SLL + `BailErrorStrategy`; bail (ParseCancellationException / Throwable) → trả 1; parse SLL sạch → 0 | SLL-only prediction không rơi vách đá trên file sạch-lớn; bail chặn error-recovery LL. `syntaxErrors` chỉ cần 0/>0 cho mọi consumer (has_error, retry-set, message) |
| AD-02 | **Bail khi SLL rơi vào full-context fallback**: listener `reportAttemptingFullContext` → throw ParseCancellationException | Full-context simulation (`execATNWithFullContext`/`closure_`) là chính chỗ phát sinh vách đá (jstack). SLL thuần có DFA bound; cắt nhánh đó = giới hạn trên hằng số nhỏ. Thay vì wall-clock deadline (cần executor + thread bỏ chạy), cắt đúng nguyên nhân |
| AD-03 | Scanning lines: `[vb6][worker] scanning i/N file=<rel>` in TRƯỚC countSyntaxErrors; dùng chung `WorkerRunner.PROGRESS_PREFIX`; N = số entry đọc được (khớp population của countSyntaxErrors) | Người chạy thấy ngay tiền-xử lý kẹt file nào; prefix là filter key của adapter (R1 cũ); không đụng regex summary |
| AD-04 | Tách recipe SLL thành static package-private helper dùng chung (WorkerRunner.parseStageSll gọi lại; countSyntaxErrors gọi lại) | Một nguồn duy nhất, tránh drift giữa stage parse và pre-scan; cả 2 class cùng package |
| AD-05 | `error_nodes` semantics đổi: exact LL count → 0/1; `has_error` giữ nguyên; ghi chú trong payload docs + verification-report | Không test/consumer nào đọc giá trị đếm (agent report §2); đổi lại được bounded time cho cả corpus |
| AD-06 | Không đụng default `VB6_WORKER_SLL` (batch two-stage OFF như go/no-go 261002-1410); pre-scan SLL là quyết định nội bộ worker, không đọc env đó | Go/no-go cũ đo trên corpus khác (sample corpus ~1s); flipping default là relitigate — tách sang follow-up nếu VERIFY trên corpus legacy cho bằng chứng mới |

### Pipeline sau plan

```
Vb6Worker.analyze pre-pass (mỗi entry):
    readTextLenient ─► declaredModuleName (regex)
        ─► stderr "[vb6][worker] scanning i/N file=<rel>"
        ─► countSyntaxErrorsSll:
              SLL + Bail + reportAttemptingFullContext→bail
              sạch ─► 0        bail/throw ─► 1
    ─► dedup map ─► batch ─► WorkerRunner.parseFile (parsing/parsed lines,
                              SLL hai-giai-doạ theo env, mặc định LL)
```

### Milestones / Acceptance

| # | Tiêu chí | Đo bằng |
|---|----------|---------|
| M1 | `<LARGEST_FILE>.frm` (file lớn nhất corpus) đơn lẻ: worker chạy xong (không timeout), stderr có `scanning 1/1` rồi `parsing/parsed`, wall < 10s | script chạy jar 1-file trong verification |
| M2 | Corpus legacy: end-to-end worker run hoàn tất trong workspace budget; stderr stream scanning/parsing/parsed từ vài giây đầu; worker_meta đầy đủ | verify script + log capture |
| M3 | Ngữ nghĩa payload giữ nguyên nơi định nghĩa: file sạch `has_error=false`; `malformed.bas` vẫn ok=false; `error_nodes` 0/1 được ghi chú; retry-exclusion vẫn loại file lỗi | fixture tests mới + contract suite |
| M4 | Không phá contract hiện tại | `pytest -k vb6` full suite pass |
| M5 | Số đo trước/sau (pre-pass wall, tổng wall, slowest) cho corpus legacy + sample-corpus regression | verification-report.md trong plan này |

### Phases

1. **Phase 01 — Worker: SLL pre-scan + scanning progress** (`phase-01-sll-prescan.md`)
   Gate: M1, M3, M4.
2. **Phase 02 — Verify + đo 2 corpus + docs** (`phase-02-verify-measure.md`)
   Gate: M2, M5.

### Risks / Open questions

- **R1**: SLL có thể báo lỗi ở chỗ LL chấp nhận (edge case ANTLR được tài
  liệu hóa) → file sạch bị `has_error=true` + `(1 syntax errors)` trong
  message. Chấp nhận: không consumer chức năng nào ngoài chuỗi thông báo +
  retry-set (batch-failure path hiếm); ghi vào verification-report.
- **R2**: Bail-on-full-context (AD-02) có thể bail file mà LL原本 parse
  được (chỉ chậm) → cùng hệ quả R1, thêm điều kiện "chỉ chậm". Chấp nhận vì
  primary batch vẫn parse file đó (không mất payload).
- **R3**: Refactor shared SLL helper đụng đường parse chính → parity risk.
  Che chắn: TwoStageParityTest (byte-equal payloads) + full suite.
- **R4**: Sau khi pre-pass hết treo, parse LOOP (mặc định LL) có thể lại
  chậm trên chính các file đó (hiện visible qua `parsing i/N`). VERIFY đo và
  ghi; nếu đúng → follow-up riêng (per-file guard cho loop / reconsider
  SLL default với bằng chứng đo trên corpus legacy) — KHÔNG scope creep ở
  đây.
- **R5**: `Vb6Worker.java.bak` (src + target/classes) chứa bản cũ — không
  biên dịch, không sửa; bỏ qua.
- **Open**: N của scanning (entries đếm được trước loop vì loadError phát
  hiện giữa chừng) — dùng `entries.size()` làm mẫu số, i chỉ tăng cho entry
  đọc thành công; lệch chỉ khi có file không đọc được (hiếm), chấp nhận.

## Resync / Rollback

- Rollback = revert 1 worker commit (Vb6Worker countSyntaxErrors + shared
  helper + scanning lines); vendor untouched; jar rebuild theo mtime.
- Không đổi adapter/Python nên không có resync cross-language.
- Follow-ups (ngoài scope): per-file guard cho parse loop; reconsider
  VB6_WORKER_SLL default với số đo corpus legacy; parse cache/incremental
  reparse.

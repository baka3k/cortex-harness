---
title: "vb6-antlr-worker: per-file parse progress + two-stage SLL→LL parsing"
status: complete-with-exclusions (phase 01+02 shipped; M6 go/no-go → two-stage default-OFF in adapter)
created: 2026-10-02
mode: hi-plan --full
scope: >
  Worker-side per-file parse progress/timing (stderr + worker_meta + parse_meta)
  and two-stage ANTLR parsing (SLL first, LL fallback) implemented in a
  worker-side subclass of VbParserRunnerImpl; adapter (vb6_antlr_adapter.py)
  relays stderr live when verbose and prints a slowest-files summary.
  OUT of scope: worker-side parse cache / incremental reparse, parse
  parallelization, vendor source modifications, changes to the stdout JSON
  protocol, python-side fallback (regex engine) behavior.
blockedBy: []
blocks: []
relatedPlans:
  - docs/plans/260917-1628-vb6-antlr-depth-upgrade
  - docs/plans/260924-1434-vb6-anchor-graph-coverage
  - docs/plans/261002-1511-vb6-worker-sll-prescan (follow-up: pre-pass
    countSyntaxErrors LL cliff — jstack 2026-10-02 cho thấy hang thật nằm
    ở tiền-xử lý, KHÔNG ở batch parse loop mà plan này đo/tối ưu)
sources:
  - docs/plans/261002-1410-vb6-worker-progress-sll/research (inline session notes)
  - code-tiny/tools/vb/antlr_worker/vendor/proleap-vb6-parser/src/main/java/io/proleap/vb6/asg/runner/impl/VbParserRunnerImpl.java
  - code-tiny/tools/vb/antlr_worker/worker/src/main/java/io/cortex/vb6/worker/Vb6Worker.java
  - code-tiny/tools/vb/vb6_antlr_adapter.py
  - jstack live evidence: parse thread ~100% CPU inside ANTLR
    ParserATNSimulator/SingletonPredictionContext during a 230-file workspace
    parse (2026-10-02 session log)
---

# vb6-antlr-worker: per-file parse progress + two-stage SLL→LL parsing

## Overview

Every sync re-parses the whole VB6 corpus in one whole-program ANTLR batch
(AD-02 of plan 260917-1628). Two observed problems:

1. **Zero visibility**: the worker prints nothing until the whole batch ends,
   so a multi-minute parse looks "stuck" and pathological files are
   unidentifiable.
2. **Prediction cost cliff**: jstack shows the parse thread living inside
   ANTLR ALL(*) adaptive prediction (`ParserATNSimulator.closure_`,
   `SingletonPredictionContext.equals`) — per-file cost is wildly
   non-uniform, which makes any fixed workspace timeout borderline.

This plan adds per-file progress/timing (stderr + meta) and the canonical
ANTLR two-stage parse (SLL first, full LL reparse on bail) to cut the
prediction cost without changing parse results.

### Coverage matrix

| Cần biết | Evidence |
|---|---|
| No public per-file accumulate API; `parseFile(File, Program, params)` is `protected` | VbParserRunnerImpl.java:248-267 |
| `analyzeFiles` = new Program → registerModelElements → for-each `parseFile` → `analyze(program)` | VbParserRunnerImpl.java:143-155 |
| Subclass in `io.cortex.vb6.worker` can call/override `protected` members (any package) | VbParserRunnerImpl.java:56,198,248 |
| Parser runs default LL; no `setPredictionMode` anywhere | grep antlr_worker/ = 0 hits |
| ANTLR 4.7.2; `PredictionMode`, `BailErrorStrategy`, `ParseCancellationException` present in shaded jar | vendor pom.xml:24-37; jar listing |
| `ignoreSyntaxErrors=true` keeps ANTLR `ConsoleErrorListener` → stderr has noise today | VbParserRunnerImpl.java:203-219 |
| Adapter discards stderr on rc==0; stdout = one JSON contract | vb6_antlr_adapter.py:292-313; Vb6Worker.java:17-20 |
| verbose plumbing exists end-to-end (`--verbose` → adapter `verbose`) | vb_analyzer_base.py:579-586,2417 |
| `parse_meta.worker_elapsed_ms` hardcoded 0 | Vb6Worker.java:649 |
| worker_meta field block is additive-safe (no test asserts exact shape) | tests/test_vb6_antlr_worker_contract.py:61-126 |

### Scope Challenge (3 câu)

1. **Làm gì?** Nhìn thấy tiến độ parse theo file trong lúc chạy + đo được file
   nào chậm; giảm thời gian parse bằng SLL-first parsing mà **không đổi kết
   quả parse**.
2. **Trong/ngoài scope?** Trong: worker subclass, stderr protocol, adapter
   relay/summary, meta mới. Ngoài: sửa vendor source, parse cache, đa luồng,
   đổi stdout protocol.
3. **Khi nào xong?** Run sync verbose thấy tiến độ per-file real-time; summary
   `slowest file` in sau parse; fixture parity: payload hai chế độ SLL+LL và
   LL-only **giống hệt nhau**; đo trên corpus legacy 230-file cho con số
   trước/sau trong verification-report.md.

### Architecture Decisions

| AD | Quyết định | Lý do |
|----|-----------|-------|
| AD-01 | Worker-side subclass `WorkerRunner extends VbParserRunnerImpl` override `parseFile` + `parseCode`; KHÔNG sửa vendor | Vendor pinned commit; protected methods overridable cross-package; tránh drift upstream (VENDOR.md) |
| AD-02 | Progress lines ra **stderr** với prefix `[vb6][worker]`; stdout giữ nguyên 1-JSON protocol | stdout = machine contract; stderr đã nhiễu sẵn (ConsoleErrorListener) nên prefix là bắt buộc để filter |
| AD-03 | Adapter dùng `Popen` + thread drain stdout riêng; stderr relay live khi `verbose`; summary `slowest files` in luôn (không cần verbose) | Tránh pipe deadlock; người chạy sync nhìn thấy tiến độ mà không cần nhớ bật verbose |
| AD-04 | Two-stage: SLL + `BailErrorStrategy` trước; `ParseCancellationException` → reparse đầy đủ LL (giống hệt vendor tail) | Canonical ANTLR two-stage; parity tuyệt đối vì file bail chạy lại LL nguyên vẹn |
| AD-05 | Đếm `sll_fallback_files` + `parse_ms_total` + `parse_slowest_*` vào worker_meta; `parse_meta.worker_elapsed_ms` điền thật | Observability ở 2 tầng (batch + per-file); không test nào assert exact-shape meta |
| AD-06 | Workspace/subprocess timeout giữ nguyên logic scale hiện tại | Vừa fix ở session 2026-10-02; SLL chỉ làm dư thời gian |

### Pipeline sau plan

```
vb_analyzer_base (--verbose) ──► adapter.parse_vb6_files_with_antlr
    └─ Popen(java worker) ── stdout: 1 JSON (giữ nguyên)
                           └─ stderr: "[vb6][worker] parsed i/N file=… ms=… [sll_fallback]"
                                  └─ verbose ? relay live : thu thập
                                      └─ sau parse: "[vb6][engine] parse: N files in Ts; slowest: F (ms)"
WorkerRunner(VbParserRunnerImpl):
    parseFile ─► timing + stderr line ─► super.parseFile
    parseCode ─► stage SLL+Bail ─► ok? ─► vendor tail (moduleName + visitor)
                              └─ bail? ─► stage LL nguyên bản ─► vendor tail
```

### Milestones / Acceptance

| # | Tiêu chí | Đo bằng |
|---|----------|---------|
| M1 | Worker in per-file progress + timing ra stderr, prefix `[vb6][worker]` | test mới: chạy jar trên fixture, assert stderr có N dòng, parse_ms hợp lệ |
| M2 | worker_meta có `parse_ms_total`, `parse_slowest_file`, `parse_slowest_ms`, `sll_fallback_files`; `parse_meta.worker_elapsed_ms` > 0 | contract test mới (key-specific assertions) |
| M3 | Adapter relay stderr live khi `--verbose`; in summary slowest không cần verbose | unit test Popen relay với fake process |
| M4 | Parity: payload hai chế độ giống hệt trên fixture corpus | parity test: chạy runner LL-only vs SLL-first so sánh payload JSON |
| M5 | Không break contract hiện tại | `pytest -k vb6` full suite pass (điểm neo: 130+ test) |
| M6 | Đo được improvement thật trên corpus legacy lớn | verification-report.md: bảng before/after (LL-only vs two-stage), tỉ lệ sll_fallback |

### Phases

1. **Phase 01 — Per-file progress + timing (worker + adapter)** (`phase-01-worker-progress.md`)
   Gate: M1, M2, M3.
2. **Phase 02 — Two-stage SLL→LL trong WorkerRunner** (`phase-02-two-stage-parse.md`)
   Gate: M4, M5.
3. **Phase 03 — Verify + đo trên corpus legacy + docs** (`phase-03-verify-measure.md`)
   Gate: M6 + resync note.

### Risks / Open questions

- **R1 (red-team H1)**: stderr đã có nhiễu `line x:y` từ ConsoleErrorListener
  với file lỗi cú pháp → adapter filter dòng theo prefix `[vb6][worker]`;
  progress parser chỉ nhận dòng đúng format.
- **R2 (H2)**: relay stderr bằng Popen có thể deadlock nếu stdout đầy buffer →
  drain stdout bằng thread riêng (bắt buộc, không optional).
- **R3 (H3)**: file lỗi cú pháp thật khiến SLL bail → parse 2 lần (SLL+LL) →
  tệ hơn LL-only. Đo `sll_fallback_files` ở M6; nếu tỉ lệ cao, cân nhắc chỉ
  SLL cho file `.bas/.cls` sạch (heuristic sau, ngoài scope này).
- **R4 (H4)**: retry path gọi `analyzeFiles` lần 2 → file bị tính thời gian 2
  lần → lấy timing của lần chạy cuối cùng; meta ghi rõ `batch_retried=true`.
- **R5 (H5)**: bằng chứng jstack là 1 run anegdote — improvement SLL phải được
  đo (M6), không assumed.
- **Open**: số dòng stderr với 230+ file khi relay live có làm console quá
  ồn? → relay live chỉ khi verbose (mặc định off), summary luôn 1 dòng.
- **Cập nhật 2026-10-02 (user override của AD-03)**: progress relay live
  LUÔN (không cần `--verbose`) — người chạy sync bắt buộc thấy tiến độ; ANTLR
  console noise vẫn verbose-only. Thêm dòng start `[vb6][worker] parsing
  i/N file=<rel>` in TRƯỚC khi parse từng file: file chậm nhiều phút vẫn
  gọi tên được file đang xử lý (trả lời trực tiếp "treo hay đang làm gì").
- **Kết quả M6 (2026-10-02, verification-report.md)**: corpus legacy 228-file
  parse hết ~3.5s (cost cliff jstack không còn tái diễn — khả năng cao nhờ fix
  charset `c7ae172` cùng ngày). R3 xác nhận thực nghiệm: 33.3% file bail SLL
  (50% trong số file parse-ok), file bail là chính các file chậm nhất
  (SLL + LL reparse = 2 lần). Go/no-go rule → **NO-GO**: adapter spawn worker
  với `VB6_WORKER_SLL=0` mặc định; `VB6_WORKER_SLL=1` opt-in; worker-side
  default giữ nguyên cho jar chạy trực tiếp. Per-file progress (feature 1)
  giữ nguyên và mặc định bật.

### Follow-ups (ngoài scope plan này)

- Worker-side parse cache / incremental reparse (trả chi phí ~1-2s/file mỗi
  sync khi corpus không đổi).
- Parse đa luồng (per-file independent parse rồi merge Program).
- Unhide `parse_meta.worker_elapsed_ms=0` tương tự cho các plane khác nếu có.

## Resync / Rollback

- Rollback = `git revert` worker subclass + adapter Popen relay; vendor untouched
  nên không cần resync vendor. Jar rebuild theo mtime stamp tự động.
- Nếu M6 cho thấy SLL không có lợi (fallback quá cao): disable two-stage bằng
  1 flag trong WorkerRunner (mặc định bật), giữ per-file progress — hai feature
  tách rời, rollback độc lập.

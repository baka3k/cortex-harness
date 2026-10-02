# VB6 worker per-file parse progress + two-stage SLL→LL — 2026-10-02

## Context

Plan `docs/plans/261002-1410-vb6-worker-progress-sll/` (mode `hi-plan --full`,
executed via `hi-craft --full` same day). Two complaints drove it: (1) a
whole-program ANTLR reparse of the VB6 corpus printed nothing until the batch
ended, so a multi-minute parse looked stuck and pathological files were
unidentifiable (jstack showed the parse thread pinned in ANTLR ALL(*)
prediction — `ParserATNSimulator.closure_` / `SingletonPredictionContext` —
during a ~230-file workspace parse); (2) the plan hoped the canonical ANTLR
two-stage parse (SLL first, LL reparse on bail) would cut prediction cost.
Scope: worker-side subclass only — vendor source untouched (VENDOR.md),
stdout one-JSON contract untouched.

## Change

- `WorkerRunner extends VbParserRunnerImpl` (new,
  `code-tiny/tools/vb/antlr_worker/worker/src/main/java/io/cortex/vb6/worker/WorkerRunner.java`):
  overrides `parseFile` for per-file wall timing + stderr progress —
  `[vb6][worker] parsing i/N file=<rel>` emitted **before** each parse (a
  multi-minute file still names itself) and
  `[vb6][worker] parsed i/N file=<rel> ms=<ms>[ sll_fallback]` after (`ms`
  anchored last so file names with spaces survive the adapter regex, H1).
  Overrides `parseCode` for two-stage: SLL + `BailErrorStrategy` with lexer AND
  parser listeners removed (console stays clean), on
  `ParseCancellationException` reparse from scratch in full LL with
  vendor-exact error handling — payload parity with LL-only is therefore
  byte-level (M4 parity test). Vendor visitor tail mirrored, marked
  "keep in sync". Aggregates reset per batch run so the failure-retry path
  keeps last-run-wins timing (R4).
- `Vb6Worker.java`: runner swapped to `WorkerRunner`; `worker_meta` gains
  `parse_ms_total` / `parse_slowest_file` / `parse_slowest_ms` /
  `sll_fallback_files` / `batch_retried`; per-file
  `parse_meta.worker_elapsed_ms` now carries the real parseFile time
  (was hardcoded 0). The analyze executor thread became a daemon so the
  post-timeout allFailed JSON path exits the JVM immediately (regression
  checked live: 228-file manifest + `--workspace-timeout-ms 500` → rc=0,
  228/228 ok=false, JVM exit in 0.56s).
- `vb6_antlr_adapter.py`: `subprocess.run` → `Popen` with a dedicated stdout
  drain thread (pipe-deadlock guard, R2) plus a watchdog that kills the
  worker and raises `TimeoutExpired` past the scaled deadline. Progress
  lines relay live in EVERY run (user override of AD-03 — never swallow
  parse progress); ANTLR console noise stays `--verbose`-only; after parse
  one summary always prints:
  `[vb6][engine] parse: N files in Ts; slowest: F (Xms)` (+ top-5 slowest
  when verbose). `worker_meta` is the authoritative aggregation source;
  stderr rows only fill gaps.
- Tests: `tests/test_vb6_worker_progress.py` (17 tests — jar-backed progress
  protocol + meta fields, two-stage/LL-only parity byte-equal on the fixture
  corpus + a nested If/Case stress fixture, fake-process relay/summary/
  timeout/rc!=0 behavior, adapter SLL default, retry-marker aggregation).
  Full `-k vb6` suite: 146 passed.

## Review cycle (full mode)

Adversarial review (code lens): score 8/10, zero criticals, three Mediums —
all fixed and re-verified:

1. Env-sensitive tests: jar-backed tests now normalize an ambient
   `VB6_WORKER_SLL` (setdefault `1`), the adapter default-off test patches
   the environment.
2. Summary double-count on the batch-retry path: the worker prints a
   `[vb6][worker] retry <K>/<N> files after batch failure` marker before the
   second `analyzeFiles` run and the adapter aggregates only rows after the
   LAST marker — counts now match worker_meta's last-run aggregation (R4).
3. Temp-copy leak on the timeout path: the daemon analyze thread dies before
   `deleteBatchTempCopies`, so UTF-8 batch copy dirs leaked on every timed-out
   legacy-encoding run. A JVM shutdown hook now deletes them (proven live:
   legacy-encoded file + 100ms timeout → dir created mid-run, gone at exit).

## Go/no-go on SLL (M6, decided NO-GO → default OFF)

Measured on the real legacy workspace
`~/REDACTED_HOST/test/SAMPLE_CORPUS` (228 VB6 files, 2 runs per
config, warm run reported; full tables in the plan's `verification-report.md`):

- The multi-minute cost cliff does **not** reproduce on the current jar —
  whole corpus parses in ~3.5s wall. Most plausible cause: the legacy-charset
  fallback fix (commit `c7ae172`, same day) removed the malformed-token
  streams that drove ALL(\*) prediction loops; R5's "one anecdote is not
  evidence" cut both ways.
- Two-stage aggregate `parse_ms_total` 826ms vs 994ms LL-only (−17%), but
  wall clock slightly worse, and **33.3% of files bail SLL (50% among
  successfully parsed files)** — R3's double-parse pathology confirmed
  empirically: every file in the SLL run's slowest-5 ok list is a fallback
  file paying SLL attempt + full LL reparse.
- Per the plan's own rule (`sll_fallback_files / N > 30%` → off): the adapter
  now spawns the worker with `VB6_WORKER_SLL=0` by default; `VB6_WORKER_SLL=1`
  opts back in; direct jar runs keep the worker-side default. Feature ships,
  off by default — plan status `complete-with-exclusions`. Per-file progress
  (feature 1) stays on unconditionally.

## Verification

- M1–M5 gates: 143 vb6 tests green, incl. 15 new
  (`tests/test_vb6_worker_progress.py`).
- M6: measured before/after on 228-file corpus; go/no-go rule applied →
  default OFF; `docs/plans/261002-1410-vb6-worker-progress-sll/verification-report.md`.
- Timeout-path regression re-checked live post-change (allFailed JSON +
  0.56s JVM exit).
- Verbose end-to-end on the corpus: live per-file progress, single summary
  line, verbose top-5.

## Impact

- Every sync using the ANTLR engine now shows live per-file parse progress
  and a slowest-file summary without `--verbose`; `parse_meta.worker_elapsed_ms`
  is real per-file data (was hardcoded 0) — risk: low (stdout contract
  unchanged, stderr additive).
- Operators on legacy-charset corpora: timed-out runs no longer leak
  `vb6enc*` temp dirs (shutdown hook; ~2200 historical leak dirs remain in
  the dev TMPDIR from pre-fix runs — safe to delete when no parse is live).
- SLL is a no-op by default in the product path; direct-jar users keep the
  worker-side default. No parse-result changes anywhere (parity byte-equal).

## Decision

- Go/no-go applied the plan's pre-registered R3 rule instead of judging the
  −17% aggregate parse-time win: a 33% bail rate means real-world corpora
  (which contain syntax-quirky files) pay double parses on exactly the files
  that are slowest, and the measured corpus showed the original cost cliff
  had already vanished (charset fix `c7ae172`). Alternative kept available:
  `VB6_WORKER_SLL=1` re-enables with zero code change, and per-file progress
  makes any future cliff observable the moment it returns.
- Vendor untouched via a `WorkerRunner` subclass (AD-01) so the pinned
  ProLeap commit stays resync-safe; the mirrored visitor tail is marked
  "keep in sync" with the pinned commit.

## References

- plan: ./docs/plans/261002-1410-vb6-worker-progress-sll/plan.md
- verification: ./docs/plans/261002-1410-vb6-worker-progress-sll/verification-report.md
- commit: c99bb95
- worker: code-tiny/tools/vb/antlr_worker/worker/src/main/java/io/cortex/vb6/worker/WorkerRunner.java
- adapter: code-tiny/tools/vb/vb6_antlr_adapter.py:273-344 (Popen/drain/watchdog), :396-431 (summary)

## Follow-ups

- Worker-side parse cache / incremental reparse (the ~1-2s/file assumption
  is now measured at ~4ms/file on healthy input — cache value shifted from
  "avoid minutes" to "avoid JVM startup + program merge").
- Revisit SLL if a pathological corpus reappears: `VB6_WORKER_SLL=1` flips it
  back with no code change; the per-file progress lines make the cliff
  observable the moment it returns.

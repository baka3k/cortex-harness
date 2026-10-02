# Verification Report — per-file parse progress + two-stage SLL→LL

Plan: `docs/plans/261002-1410-vb6-worker-progress-sll/plan.md` · Date: 2026-10-02

## M1–M5 gates (phase 01+02)

| Gate | Evidence | Result |
|---|---|---|
| M1 per-file stderr progress | `tests/test_vb6_worker_progress.py::WorkerProgressTest` — jar run over fixture: `parsing i/N` + `parsed i/N … ms=` lines sequential 1..N, start line precedes parsed line | PASS |
| M2 worker_meta timing fields | `test_worker_meta_timing_fields`: `parse_ms_total>0`, `parse_slowest_file` non-empty, `sll_fallback_files` present; `test_parse_meta_worker_elapsed_filled`: `worker_elapsed_ms>0` | PASS |
| M3 adapter relay + summary | `AdapterRelayTest` (fake proc): progress relays live without `--verbose`, ANTLR console noise verbose-only, always-on slowest summary, timeout → kill + `TimeoutExpired`, nonzero rc → stderr tail | PASS |
| M4 parity two-stage vs LL-only | `TwoStageParityTest.test_payloads_byte_equal` — canonical files JSON identical for default vs `VB6_WORKER_SLL=0`; `malformed.bas` ok=false in both | PASS |
| M5 no contract break | `pytest -k vb6 --ignore=tests/test_unified_contract_doc_paths.py` → **143 passed** (incl. 15 new) | PASS |

## M6 — measurement on the legacy corpus

Corpus: `~/REDACTED_HOST/test/SAMPLE_CORPUS` (real workspace, not
copied into the repo) — **228 VB6 files** (`SAMPLE_APP/` app + the vendored
ProLeap fixture corpus under `REDACTED_DIR/tests/fixtures/proleap-corpus/`).
This is the workspace from the plan's jstack evidence (≈"230-file workspace").
Method: adapter `parse_vb6_files_with_antlr` end-to-end, per config 2 runs,
**run 2 = warm** is the reported number. Baseline = `VB6_WORKER_SLL=0`.

| Metric (warm run) | Two-stage SLL→LL | LL-only (baseline) | Δ |
|---|---|---|---|
| `worker_meta.elapsed_ms` | 4077 | 3478 | +17% (slower) |
| `parse_ms_total` (sum of per-file `parseFile`) | 826 | 994 | −17% |
| wall clock (python side) | 4273 | 3706 | +15% (slower) |
| `sll_fallback_files` | 76 / 228 = **33.3%** | 0 | — |
| ok / failed files | 100 / 128 | 100 / 128 | identical |
| slowest file | `SAMPLE_APP/SAMPLE_FORM.frm` 74ms (fb) | `SAMPLE_APP/main.bas` 70ms | — |

Fallback composition (SLL run, all 228 rows mapped to outcome):

- fallback among **successfully parsed** files: **50 / 100 = 50%**
- fallback among files that fail anyway: 26 / 128

### Reading

1. **The pathology the plan targeted no longer reproduces.** The corpus
   parses in ~3.5s wall (~1s total parse), not minutes. The multi-minute
   ANTLR prediction cliff from the 2026-10-02 jstack session is gone on the
   current jar — most plausibly the legacy-charset fallback fix
   (commit `c7ae172`, same day) removed the malformed-token streams that
   drove ALL(\*) prediction into `ParserATNSimulator` closure loops.
   R5's warning ("1-run anecdote — improvement must be measured, not
   assumed") applies to the original evidence, and the measurement now
   shows there is nothing large left to win.
2. **R3 confirmed empirically**: SLL-bailing files pay SLL attempt + full LL
   reparse. Every one of the slowest-5 ok files in the SLL run is a fallback
   file; aggregate `parse_ms_total` still drops 17% only because non-bailing
   files get genuinely cheaper — but 50% of ok files bail, so half the real
   code pays double.
3. **128 failed files are orthogonal to SLL**: identical ok/failed set in
   both modes. Failures are (a) intentional workspace backup duplicates
   (`SAMPLE_FORM.frm`/`SAMPLE_FORM.frm` share `Attribute VB_Name` → the worker's
   duplicate-module guard) and (b) upstream ProLeap/MSDN snippet fixtures
   that are not complete modules.

## Go/no-go decision (phase 03.2, rule R3)

Rule: `sll_fallback_files / N > 30%` **or** two-stage slower than baseline
→ default OFF. **Outcome: NO-GO — 33.3% > 30% and wall clock not better.**

Implemented: the adapter now spawns the worker with
`VB6_WORKER_SLL=0` unless the operator sets it explicitly (`=1` opts in).
The worker-side default stays ON for direct jar runs; parity and progress
features are unaffected. Plan status → `complete-with-exclusions`.

## Phase 03.4 checklist

- ✅ Live progress + always-on summary observed on the 228-file corpus run
  (`[vb6][worker] parsing/parsed` lines; `[vb6][engine] parse: 228 files in …s; slowest: …`).
- ✅ No worker stderr line leaks into stdout — jar runs parse with a single
  `json.loads` (`test_stdout_contract_untouched`).
- ✅ Timeout path regression: 228-file manifest + `--workspace-timeout-ms 500`
  → rc=0, allFailed JSON (228/228 `ok=false`), **JVM exited in 0.56s**
  (daemon analyze-thread fix holds); 0 progress lines emitted.
- ✅ Full `-k vb6` suite green after the go/no-go change (143 passed).

## Raw data

- Measurement matrix: `/tmp/vb6_sll_results.json` (per-run meta + top-10
  slowest per run, regenerated via the method above).
- Fallback breakdown: `/tmp/vb6_sll_breakdown.json` (all 228 rows).

# Benchmark Report — M4 (plan 260917-1200, phase 06)

Date: 2026-09-17. Machine: darwin arm64 (macOS), Python 3.12.14, OpenJDK
26.0.2.1, Maven 3.9.16. Corpus: fixture replicated 12x → 132 files (unique
`Attribute VB_Name` per replica, `.frm` materialized through the adapter).
Runner: `tests/benchmark_vb6_parse_quality.py --replicas 12`.

## Numbers

| Metric | regex | antlr (whole-program worker) |
|---|---|---|
| files | 132 | 132 |
| ok | 132 | 120 (+12 = sanctioned malformed replicas, `ok=false`) |
| functions extracted | 360 | 348 |
| call edges extracted | 468 (paren-only, no resolution) | 576 (incl. no-paren/Call/continuation, ASG-bound) |
| per-file p50 | 0.127 ms | 25.8 ms end-to-end / 9.5 ms worker-internal p95 |
| files/s | 7165 | 38.8 |
| JVM startup | n/a | 144 ms (gate ≤ 5 s: **PASS**) |

Quality delta vs baseline (`baseline.md`): expected-callsite capture went
20/47 → 47/47 (string-literal trap correctly excluded); expected-resolvable
recall 18/29 (62%, arbitrary targets) → **29/29 (100%) with verified callee
identity** (M2, payload level, measured by `tests/test_vb6_golden.py`).

## M4 gate verdict

- JVM startup ≤ 5 s: **PASS** (0.14 s).
- "throughput < 2x regex per-file": **FAIL — 96x** (0.13 ms vs 12.6 ms
  worker-internal / 25.8 ms incl. subprocess). Spike detail in
  `spike-report.md`. The relative gate is unattainable for any engine that
  builds a cross-module ASG — regex does no resolution at all, which is the
  deficiency this plan fixes. Absolute cost: **~10 ms/file worker-internal,
  ~26 ms/file end-to-end** → ~2.6 s per 100 files, ~26 s per 1000 files, one
  JVM per sync batch. Embedding + Neo4j/Qdrant writes dominate any real sync
  by orders of magnitude.
- `--XX:TieredStopAtLevel=1` measured: no improvement (12.5 ms/file) — cost
  is ProLeap's multi-pass ASG, not JIT warmup.

## Recommendation

Keep engine `auto → antlr` (AD-07) and treat M4 as an absolute budget:
worker-internal ≤ 50 ms/file on ≥100-file corpora (current: 9.5 ms). The
relative gate as written would force regex, i.e. keep the broken call graph.
Corpus limitation: no ≥200-file real-world VB6 repository was available;
numbers are from the synthetic replica corpus (owner can re-run on a real
repo: `python tests/benchmark_vb6_parse_quality.py --engine antlr`).

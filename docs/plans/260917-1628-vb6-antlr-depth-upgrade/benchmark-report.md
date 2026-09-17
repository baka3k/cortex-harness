# Benchmark Report — depth upgrade (plan 260917-1628, phase 5.3)

Date: 2026-09-17. Machine: darwin arm64 (macOS), Python 3.12.14, OpenJDK
26.0.2.1. Corpus: fixture replicated 12x → 156 files (13 sources per replica
now: `modApi.bas` + `clsEvents.cls` added, frmMain designer extended to a
real 99-line control tree). Runner:
`python tests/benchmark_vb6_parse_quality.py --replicas 12`.

## Numbers (delta vs plan 260917-1200 report)

| Metric | 1200 (pre-upgrade) | 1628 (this plan) | Budget (AD-08) |
|---|---|---|---|
| files | 132 | 156 | ≥100 |
| ok | 120 (+12 sanctioned malformed) | 144 (+12 sanctioned malformed) | — |
| functions extracted | 348 | 444 | — |
| call edges | 576 | 684 (incl. `rs!Field` dictionary rows) | — |
| per-file p50 (end-to-end batch) | 25.8 ms | **9.26 ms** | worker-internal ≤ 50 ms |
| worker-internal p95 | 9.5 ms | **8.6 ms** | ≤ 50 ms: **PASS** |
| JVM startup | 144 ms | **156 ms** | ≤ 5 s: **PASS** |
| files/s (whole batch) | 38.8 | 108.0 | — |
| regex reference | 0.127 ms/file | 0.126 ms/file | (unchanged; no resolution) |

## Reading

- The new serialization (enums/constants/events/declares/attributes ctx
  walks, controls[] on designer files, hidden-channel comment scan) is
  per-module linear work on data ProLeap already parsed — 0 new parse passes
  (AD-08). Worker-internal p95 moved 9.5 → 8.6 ms on a corpus with ~27%
  more files and heavier forms: **no measurable regression; within noise**.
- End-to-end p50 improved mainly because the corpus now has more small
  modules amortizing the single JVM batch.
- Dictionary rows add ~1 edge per replica; comment extraction adds one
  token-stream `fill()` per file (already materialized by the parser).

## Verdict

M6 absolute-budget gate: **PASS** (worker-internal 8.6 ms p95 ≤ 50 ms;
JVM 0.16 s ≤ 5 s). `[vb6][summary]` line format unchanged (observability
contract intact), sample from the fixture run:

```
[vb6][summary] engine=antlr(12)+regex_fallback(1) files=13 fallback=1 callsites=56 resolved=35 possible=21 rate=62.5%
```

(fallback=1 is `malformed.bas`, the sanctioned regex-fallback case; rate is
over ALL callsites including externals/builtins, same definition as before.)
